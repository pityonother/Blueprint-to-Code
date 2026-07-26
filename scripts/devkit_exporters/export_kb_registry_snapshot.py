r"""Export a sanitized ARK DevKit Asset Registry snapshot.

This file must run inside ARK DevKit's Unreal Python environment.  The host
process supplies only the output location and a small set of runtime switches:

    BTC_KB_REGISTRY_OUTPUT=<working directory>
    BTC_KB_REGISTRY_DEPENDENCIES=0|1       (default: 1)
    BTC_KB_REGISTRY_BATCH_SIZE=<positive integer>  (default: 500)

The exporter publishes:

    registry_manifest.json
    generations/<generation-id>/registry_assets.jsonl
    generations/<generation-id>/registry_dependencies.jsonl
    generations/<generation-id>/registry_checkpoint.json

Only Asset Registry identities, a small tag allowlist, package flags, and
package-level dependency edges are exported.  Local filesystem paths are never
embedded in these files.  Work is committed one batch at a time inside a
private staging directory so a repeated run can resume safely after
interruption.  A verified generation is renamed into its immutable location,
then the root manifest is replaced atomically as the publication commit point.
"""

from __future__ import print_function

import datetime
import hashlib
import json
import os
import re
import uuid

try:
    import unreal
except Exception:
    unreal = None


SCHEMA_VERSION = "ark.kb.registry-snapshot.v2"
CHECKPOINT_SCHEMA = "ark.kb.registry-checkpoint.v2"
ASSET_ROW_SCHEMA = "ark.kb.registry-asset.v1"
DEPENDENCY_ROW_SCHEMA = "ark.kb.registry-dependency.v1"

ASSET_OUTPUT_NAME = "registry_assets.jsonl"
DEPENDENCY_OUTPUT_NAME = "registry_dependencies.jsonl"
MANIFEST_OUTPUT_NAME = "registry_manifest.json"
CHECKPOINT_OUTPUT_NAME = "registry_checkpoint.json"
STAGING_DIRECTORY_NAME = ".registry_snapshot_work"
GENERATIONS_DIRECTORY_NAME = "generations"
MANIFEST_STAGING_NAME = ".registry_manifest_to_publish.json"

TAG_ALLOWLIST = (
    "GeneratedClass",
    "ParentClass",
    "NativeParentClass",
    "BlueprintType",
    "IsDataOnly",
    "DataOnly",
    "ImplementedInterfaces",
)

# UE 5.5 exposes these as the five booleans on
# unreal.AssetRegistryDependencyOptions.
DEPENDENCY_TYPES = (
    ("hard_package", "include_hard_package_references"),
    ("soft_package", "include_soft_package_references"),
    ("searchable_name", "include_searchable_names"),
    ("soft_management", "include_soft_management_references"),
    ("hard_management", "include_hard_management_references"),
)
ALL_DEPENDENCY_OPTION_NAMES = tuple(item[1] for item in DEPENDENCY_TYPES)
UNRESOLVED_DEPENDENCY_TYPE = "unresolved_identifier"

DEFAULT_BATCH_SIZE = 500
MAX_BATCH_SIZE = 10000
MAX_RECORDED_ERRORS = 100

_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/]|\\\\\?\\[a-z]:\\|\\\\[^\\/\r\n]+[\\/][^\\/\r\n]+)"
)
_FILE_URI_RE = re.compile(r"(?i)\bfile:(?:/{2,3}|\\\\)")
_COMMON_LOCAL_UNIX_PATH_RE = re.compile(
    r"(?i)(?:^|[\s\"'])/(?:users|home|root|tmp|var|etc|usr|opt|mnt)/"
)


def _utc_now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _log(message):
    text = "[KBRegistrySnapshot] " + str(message)
    print(text)
    if unreal is not None:
        try:
            unreal.log(text)
        except Exception:
            pass


def _sanitize_text(value):
    """Return a bounded string with local paths removed.

    Registry object/package paths such as /Game, /Script, and plugin mount
    points remain intact.  This check targets host filesystem forms only.
    """

    try:
        text = str(value or "")
    except Exception:
        text = ""
    text = text.replace("\x00", "").strip()
    if (
        _WINDOWS_ABSOLUTE_PATH_RE.search(text)
        or _FILE_URI_RE.search(text)
        or _COMMON_LOCAL_UNIX_PATH_RE.search(text)
    ):
        return "LOCAL_PATH_REDACTED"
    return text


def _sanitize_payload(value):
    if isinstance(value, dict):
        return {
            _sanitize_text(key): _sanitize_payload(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_text(value)


def _canonical_json(value):
    return json.dumps(
        _sanitize_payload(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fsync_directory(path):
    """Best-effort directory sync after replace/rename.

    Windows does not consistently allow opening directories for fsync, so
    failure is intentionally ignored there.  File contents are always fsynced
    before this helper is called.
    """

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
    temporary_path = path + ".tmp." + str(os.getpid())
    encoded = (
        json.dumps(
            _sanitize_payload(payload),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    with open(temporary_path, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, path)
    _fsync_directory(os.path.dirname(path))


def _append_jsonl_batch(path, rows):
    if not rows:
        return os.path.getsize(path) if os.path.isfile(path) else 0
    with open(path, "ab") as handle:
        for row in rows:
            handle.write(_canonical_json(row).encode("utf-8"))
            handle.write(b"\n")
        handle.flush()
        os.fsync(handle.fileno())
        return handle.tell()


def _truncate_file(path, byte_count=0):
    mode = "r+b" if os.path.isfile(path) else "w+b"
    with open(path, mode) as handle:
        handle.truncate(max(0, int(byte_count or 0)))
        handle.flush()
        os.fsync(handle.fileno())


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


def _sha256_file(path):
    return _file_integrity(path)["sha256"]


def _contains_local_path(text):
    return bool(
        _WINDOWS_ABSOLUTE_PATH_RE.search(text)
        or _FILE_URI_RE.search(text)
        or _COMMON_LOCAL_UNIX_PATH_RE.search(text)
    )


def _assert_sanitized_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if _contains_local_path(line):
                raise RuntimeError(
                    "Sanitization check failed in {} at line {}".format(
                        os.path.basename(path),
                        line_number,
                    )
                )


def _env_bool(name, default):
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return bool(default)
    normalized = str(raw).strip().casefold()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise RuntimeError("{} must be 0 or 1".format(name))


def _env_batch_size():
    raw = str(os.environ.get("BTC_KB_REGISTRY_BATCH_SIZE") or "").strip()
    if not raw:
        return DEFAULT_BATCH_SIZE
    try:
        value = int(raw)
    except Exception:
        raise RuntimeError("BTC_KB_REGISTRY_BATCH_SIZE must be an integer")
    if value < 1 or value > MAX_BATCH_SIZE:
        raise RuntimeError(
            "BTC_KB_REGISTRY_BATCH_SIZE must be between 1 and {}".format(MAX_BATCH_SIZE)
        )
    return value


def _resolve_output_dir():
    raw = str(os.environ.get("BTC_KB_REGISTRY_OUTPUT") or "").strip().strip("\"'")
    if not raw:
        raise RuntimeError("BTC_KB_REGISTRY_OUTPUT is required")
    output_dir = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)
    return output_dir


def _read_field(value, names, default=None):
    if value is None:
        return default
    for name in names:
        try:
            result = getattr(value, name)
            if callable(result):
                result = result()
            return result
        except Exception:
            pass
        try:
            return value.get_editor_property(name)
        except Exception:
            pass
    return default


def _unreal_text(value):
    """Convert Name/TopLevelAssetPath/SoftObjectPath values consistently."""

    if value is None:
        return ""
    if isinstance(value, str):
        return _sanitize_text(value)

    package_name = _read_field(value, ("package_name",), None)
    asset_name = _read_field(value, ("asset_name",), None)
    if package_name is not None and asset_name is not None:
        package_text = _sanitize_text(package_name)
        asset_text = _sanitize_text(asset_name)
        if package_text and asset_text:
            return package_text + "." + asset_text

    for method_name in ("to_string", "export_text"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                return _sanitize_text(method())
            except Exception:
                pass
    return _sanitize_text(value)


def _integer_or_text(value):
    if value is None:
        return 0
    try:
        return int(value)
    except Exception:
        inner = _read_field(value, ("value",), None)
        try:
            return int(inner)
        except Exception:
            return _unreal_text(value)


def _asset_object_path(asset_data):
    direct = _read_field(asset_data, ("object_path",), None)
    direct_text = _unreal_text(direct)
    if direct_text:
        return direct_text

    soft_path_method = getattr(asset_data, "get_soft_object_path", None)
    if callable(soft_path_method):
        try:
            soft_path = _unreal_text(soft_path_method())
            if soft_path:
                return soft_path
        except Exception:
            pass

    package_name = _unreal_text(_read_field(asset_data, ("package_name",), ""))
    asset_name = _unreal_text(_read_field(asset_data, ("asset_name",), ""))
    if package_name and asset_name:
        return package_name + "." + asset_name
    return ""


def _asset_package_name(asset_data):
    return _unreal_text(_read_field(asset_data, ("package_name",), ""))


def _asset_tag_value(asset_data, tag_name):
    getter = getattr(asset_data, "get_tag_value", None)
    if not callable(getter):
        return ""
    try:
        return _unreal_text(getter(tag_name))
    except Exception:
        return ""


def _asset_record(asset_data):
    object_path = _asset_object_path(asset_data)
    package_name = _asset_package_name(asset_data)
    asset_name = _unreal_text(_read_field(asset_data, ("asset_name",), ""))
    package_path = _unreal_text(_read_field(asset_data, ("package_path",), ""))
    asset_class_path = _unreal_text(
        _read_field(asset_data, ("asset_class_path", "asset_class"), "")
    )
    tags = {}
    for tag_name in TAG_ALLOWLIST:
        tag_value = _asset_tag_value(asset_data, tag_name)
        if tag_value:
            tags[tag_name] = tag_value
    return {
        "schema": ASSET_ROW_SCHEMA,
        "object_path": object_path,
        "package_name": package_name,
        "package_path": package_path,
        "asset_name": asset_name,
        "asset_class_path": asset_class_path,
        "package_flags": _integer_or_text(
            _read_field(asset_data, ("package_flags",), 0)
        ),
        "tags": tags,
        "identity_status": "CONFIRMED",
        "identity_confidence": "HIGH",
        "identity_source_kind": "asset_registry",
    }


def _asset_entry_key(asset_data):
    object_path = _asset_object_path(asset_data)
    if object_path:
        return object_path
    package_name = _asset_package_name(asset_data)
    asset_name = _unreal_text(_read_field(asset_data, ("asset_name",), ""))
    if package_name and asset_name:
        return package_name + "." + asset_name
    return ""


def _load_checkpoint(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return {}
        if payload.get("schema") != CHECKPOINT_SCHEMA:
            return {}
        return payload
    except Exception:
        return {}


def _valid_generation_id(value):
    text = str(value or "")
    return bool(re.match(r"^[0-9a-f]{32}$", text))


def _resolve_published_path(output_dir, relative_path):
    """Resolve a manifest path without allowing it to escape output_dir."""

    text = str(relative_path or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or re.match(r"(?i)^[a-z]:", text):
        return ""
    candidate = os.path.realpath(os.path.join(output_dir, *text.split("/")))
    output_root = os.path.realpath(output_dir)
    try:
        if os.path.commonpath((output_root, candidate)) != output_root:
            return ""
    except Exception:
        return ""
    return candidate


def _published_manifest_if_current(
    output_dir,
    inventory_signature,
    dependency_enabled,
    asset_total,
    package_total,
):
    """Return a fully verified immutable generation, otherwise None."""

    manifest_path = os.path.join(output_dir, MANIFEST_OUTPUT_NAME)
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8-sig") as handle:
            manifest = json.load(handle)
    except Exception:
        return None
    if not isinstance(manifest, dict):
        return None
    if manifest.get("schema") != SCHEMA_VERSION:
        return None
    if str(manifest.get("status") or "") not in (
        "COMPLETE",
        "COMPLETE_WITH_WARNINGS",
    ):
        return None
    producer = manifest.get("producer")
    if (
        not isinstance(producer, dict)
        or producer.get("script") != os.path.basename(__file__)
        or producer.get("source_sha256") != _sha256_file(os.path.abspath(__file__))
    ):
        return None
    if manifest.get("inventory_signature") != inventory_signature:
        return None
    if bool(manifest.get("dependencies_enabled")) != bool(dependency_enabled):
        return None
    if int(manifest.get("asset_count") or -1) != int(asset_total):
        return None
    if int(manifest.get("package_count") or -1) != int(package_total):
        return None
    generation_id = str(manifest.get("generation_id") or "")
    if not _valid_generation_id(generation_id):
        return None

    outputs = manifest.get("outputs")
    files = manifest.get("files")
    if not isinstance(outputs, dict) or not isinstance(files, dict):
        return None
    generation_prefix = GENERATIONS_DIRECTORY_NAME + "/" + generation_id + "/"
    expected_relative_paths = {
        "assets": generation_prefix + ASSET_OUTPUT_NAME,
        "dependencies": generation_prefix + DEPENDENCY_OUTPUT_NAME,
        "checkpoint": generation_prefix + CHECKPOINT_OUTPUT_NAME,
    }
    resolved_paths = {}
    for key in ("assets", "dependencies", "checkpoint"):
        relative_path = str(outputs.get(key) or "")
        metadata = files.get(key)
        if relative_path != expected_relative_paths[key]:
            return None
        path = _resolve_published_path(output_dir, relative_path)
        if not path or not isinstance(metadata, dict):
            return None
        if str(metadata.get("path") or "") != relative_path:
            return None
        if not os.path.isfile(path):
            return None
        integrity = _file_integrity(path)
        if integrity["sha256"] != str(metadata.get("sha256") or ""):
            return None
        try:
            expected_bytes = int(metadata["bytes"])
        except Exception:
            return None
        if integrity["bytes"] != expected_bytes:
            return None
        if key in ("assets", "dependencies"):
            try:
                expected_lines = int(metadata["lines"])
                expected_records = int(metadata["record_count"])
            except Exception:
                return None
            if (
                integrity["lines"] != expected_lines
                or expected_lines != expected_records
            ):
                return None
        resolved_paths[key] = path

    if int(files["assets"]["record_count"]) != int(manifest.get("asset_count") or 0):
        return None
    if int(files["dependencies"]["record_count"]) != int(
        manifest.get("dependency_count") or 0
    ):
        return None
    try:
        with open(
            resolved_paths["checkpoint"],
            "r",
            encoding="utf-8-sig",
        ) as handle:
            checkpoint = json.load(handle)
    except Exception:
        return None
    if not isinstance(checkpoint, dict):
        return None
    if checkpoint.get("schema") != CHECKPOINT_SCHEMA:
        return None
    if checkpoint.get("generation_id") != generation_id:
        return None
    if checkpoint.get("status") != manifest.get("status"):
        return None
    if checkpoint.get("inventory_signature") != inventory_signature:
        return None
    if str(checkpoint.get("asset_output_sha256") or "") != str(
        files["assets"].get("sha256") or ""
    ):
        return None
    if str(checkpoint.get("dependency_output_sha256") or "") != str(
        files["dependencies"].get("sha256") or ""
    ):
        return None
    return manifest


def _engine_version():
    if unreal is None:
        return "UNKNOWN"
    system_library = getattr(unreal, "SystemLibrary", None)
    method = getattr(system_library, "get_engine_version", None)
    if callable(method):
        try:
            return _sanitize_text(method())
        except Exception:
            pass
    return "UNKNOWN"


def _get_registry_assets():
    if unreal is None:
        raise RuntimeError("This exporter must run inside ARK DevKit Unreal Python")
    helpers = getattr(unreal, "AssetRegistryHelpers", None)
    if helpers is None:
        raise RuntimeError("AssetRegistryHelpers is unavailable")
    registry = helpers.get_asset_registry()
    if registry is None:
        raise RuntimeError("Asset Registry is unavailable")

    _log("Requesting the complete on-disk Asset Registry view")
    search = getattr(registry, "search_all_assets", None)
    if callable(search):
        search(True)
    wait = getattr(registry, "wait_for_completion", None)
    if callable(wait):
        wait()

    get_all = getattr(registry, "get_all_assets", None)
    if not callable(get_all):
        raise RuntimeError("Asset Registry get_all_assets is unavailable")
    try:
        raw_assets = get_all(True)
    except TypeError:
        raw_assets = get_all()

    entries = []
    skipped_without_identity = 0
    for asset_data in raw_assets or []:
        key = _asset_entry_key(asset_data)
        if not key:
            skipped_without_identity += 1
            continue
        entries.append((key, asset_data))
    entries.sort(key=lambda item: item[0])

    # On-disk Asset Registry identities should be unique.  Keep the first
    # deterministic row if a transient duplicate is nevertheless returned.
    deduplicated = []
    previous_key = None
    duplicate_count = 0
    for key, asset_data in entries:
        if key == previous_key:
            duplicate_count += 1
            continue
        deduplicated.append((key, asset_data))
        previous_key = key
    return registry, deduplicated, skipped_without_identity, duplicate_count


def _inventory_signature(asset_entries, batch_size):
    digest = hashlib.sha256()
    total = len(asset_entries)
    for index, (_key, asset_data) in enumerate(asset_entries):
        digest.update(_canonical_json(_asset_record(asset_data)).encode("utf-8"))
        digest.update(b"\n")
        if (index + 1) % max(batch_size, 1000) == 0:
            _log(
                "Inventory signature: {}/{}".format(
                    index + 1,
                    total,
                )
            )
    return digest.hexdigest()


def _dependency_options(active_option_name):
    options_type = getattr(unreal, "AssetRegistryDependencyOptions", None)
    if options_type is None:
        raise RuntimeError("AssetRegistryDependencyOptions is unavailable")
    options = options_type()
    for option_name in ALL_DEPENDENCY_OPTION_NAMES:
        enabled = option_name == active_option_name
        try:
            setattr(options, option_name, enabled)
            continue
        except Exception:
            pass
        try:
            options.set_editor_property(option_name, enabled)
        except Exception:
            raise RuntimeError(
                "Unable to configure dependency option {}".format(option_name)
            )
    return options


def _dependency_option_map():
    return {
        dependency_type: _dependency_options(option_name)
        for dependency_type, option_name in DEPENDENCY_TYPES
    }


def _dependency_rows_for_package(registry, package_name, option_map, errors):
    rows = []
    for dependency_type, _option_name in DEPENDENCY_TYPES:
        try:
            dependencies = registry.get_dependencies(
                package_name,
                option_map[dependency_type],
            )
        except Exception as exc:
            if len(errors) < MAX_RECORDED_ERRORS:
                errors.append(
                    {
                        "package_name": package_name,
                        "dependency_type": dependency_type,
                        "message": _sanitize_text(exc),
                    }
                )
            continue
        targets = sorted(
            set(
                target
                for target in (_unreal_text(item) for item in (dependencies or []))
                if target
            )
        )
        for target_package_name in targets:
            exported_type = (
                dependency_type
                if target_package_name.startswith("/")
                else UNRESOLVED_DEPENDENCY_TYPE
            )
            rows.append(
                {
                    "schema": DEPENDENCY_ROW_SCHEMA,
                    "source_package_name": package_name,
                    "target_package_name": target_package_name,
                    "dependency_type": exported_type,
                    "reported_dependency_type": dependency_type,
                    "source_kind": "asset_registry",
                    "confidence": (
                        "HIGH" if exported_type == dependency_type else "LOW"
                    ),
                    "reason_code": (
                        ""
                        if exported_type == dependency_type
                        else "TARGET_NOT_PACKAGE_PATH"
                    ),
                }
            )
    return rows


def _checkpoint_is_resumable(
    checkpoint,
    producer_source_sha256,
    inventory_signature,
    dependency_enabled,
    asset_total,
    package_total,
    asset_path,
    dependency_path,
):
    if not checkpoint:
        return False
    if checkpoint.get("producer_source_sha256") != producer_source_sha256:
        return False
    if not _valid_generation_id(checkpoint.get("generation_id")):
        return False
    if checkpoint.get("inventory_signature") != inventory_signature:
        return False
    if bool(checkpoint.get("dependencies_enabled")) != bool(dependency_enabled):
        return False
    try:
        checkpoint_asset_total = int(checkpoint.get("asset_total"))
        checkpoint_package_total = int(checkpoint.get("package_total"))
        asset_bytes = int(checkpoint.get("asset_output_bytes") or 0)
        dependency_bytes = int(checkpoint.get("dependency_output_bytes") or 0)
        asset_cursor = int(checkpoint.get("asset_cursor") or 0)
        package_cursor = int(checkpoint.get("package_cursor") or 0)
        asset_rows = int(checkpoint.get("asset_rows") or 0)
        dependency_rows = int(checkpoint.get("dependency_rows") or 0)
    except Exception:
        return False
    if checkpoint_asset_total != int(asset_total):
        return False
    if checkpoint_package_total != int(package_total):
        return False

    if asset_bytes < 0 or dependency_bytes < 0:
        return False
    if asset_bytes and (
        not os.path.isfile(asset_path) or os.path.getsize(asset_path) < asset_bytes
    ):
        return False
    if dependency_bytes and (
        not os.path.isfile(dependency_path)
        or os.path.getsize(dependency_path) < dependency_bytes
    ):
        return False
    if asset_cursor > asset_total:
        return False
    if package_cursor > package_total:
        return False
    if asset_cursor < 0:
        return False
    if package_cursor < 0:
        return False
    if asset_rows != asset_cursor:
        return False
    if dependency_rows < 0:
        return False

    phase = str(checkpoint.get("phase") or "")
    if phase not in ("assets", "dependencies", "complete"):
        return False
    if phase in ("dependencies", "complete") and asset_cursor != int(asset_total):
        return False
    if (
        phase == "complete"
        and dependency_enabled
        and package_cursor != int(package_total)
    ):
        return False

    status = str(checkpoint.get("status") or "")
    if phase in ("assets", "dependencies") and status != "IN_PROGRESS":
        return False
    if phase == "complete" and status not in (
        "IN_PROGRESS",
        "COMPLETE",
        "COMPLETE_WITH_WARNINGS",
    ):
        return False

    if phase == "complete" and status != "IN_PROGRESS":
        expected_asset_hash = str(checkpoint.get("asset_output_sha256") or "")
        expected_dependency_hash = str(checkpoint.get("dependency_output_sha256") or "")
        if (
            not expected_asset_hash
            or not os.path.isfile(asset_path)
            or _sha256_file(asset_path) != expected_asset_hash
        ):
            return False
        if (
            not expected_dependency_hash
            or not os.path.isfile(dependency_path)
            or _sha256_file(dependency_path) != expected_dependency_hash
        ):
            return False
    return True


def _new_checkpoint(
    producer_source_sha256,
    inventory_signature,
    dependency_enabled,
    batch_size,
    asset_total,
    package_total,
    skipped_without_identity,
    duplicate_asset_count,
):
    now = _utc_now()
    return {
        "schema": CHECKPOINT_SCHEMA,
        "generation_id": uuid.uuid4().hex,
        "phase": "assets",
        "status": "IN_PROGRESS",
        "producer_source_sha256": producer_source_sha256,
        "inventory_signature": inventory_signature,
        "dependencies_enabled": bool(dependency_enabled),
        "batch_size": int(batch_size),
        "asset_cursor": 0,
        "asset_total": int(asset_total),
        "asset_rows": 0,
        "asset_output_bytes": 0,
        "package_cursor": 0,
        "package_total": int(package_total),
        "dependency_rows": 0,
        "dependency_output_bytes": 0,
        "dependency_counts": {
            **{
                dependency_type: 0 for dependency_type, _option_name in DEPENDENCY_TYPES
            },
            UNRESOLVED_DEPENDENCY_TYPE: 0,
        },
        "skipped_assets_without_identity": int(skipped_without_identity),
        "duplicate_asset_identities": int(duplicate_asset_count),
        "errors": [],
        "started_at": now,
        "updated_at": now,
        "completed_at": "",
    }


def _write_asset_rows(
    asset_entries,
    asset_path,
    checkpoint_path,
    checkpoint,
    batch_size,
):
    start = int(checkpoint.get("asset_cursor") or 0)
    total = len(asset_entries)
    for batch_start in range(start, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        rows = [
            _asset_record(asset_data)
            for _key, asset_data in asset_entries[batch_start:batch_end]
        ]
        checkpoint["asset_output_bytes"] = _append_jsonl_batch(
            asset_path,
            rows,
        )
        checkpoint["asset_cursor"] = batch_end
        checkpoint["asset_rows"] = int(checkpoint.get("asset_rows") or 0) + len(rows)
        checkpoint["updated_at"] = _utc_now()
        _write_json_atomic(checkpoint_path, checkpoint)
        _log("Assets: {}/{}".format(batch_end, total))


def _write_dependency_rows(
    registry,
    package_names,
    dependency_path,
    checkpoint_path,
    checkpoint,
    batch_size,
):
    option_map = _dependency_option_map()
    errors = checkpoint.setdefault("errors", [])
    counts = checkpoint.setdefault("dependency_counts", {})
    start = int(checkpoint.get("package_cursor") or 0)
    total = len(package_names)

    for batch_start in range(start, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        rows = []
        for package_name in package_names[batch_start:batch_end]:
            package_rows = _dependency_rows_for_package(
                registry,
                package_name,
                option_map,
                errors,
            )
            rows.extend(package_rows)
            for row in package_rows:
                dependency_type = row["dependency_type"]
                counts[dependency_type] = int(counts.get(dependency_type) or 0) + 1
        checkpoint["dependency_output_bytes"] = _append_jsonl_batch(
            dependency_path,
            rows,
        )
        checkpoint["package_cursor"] = batch_end
        checkpoint["dependency_rows"] = int(
            checkpoint.get("dependency_rows") or 0
        ) + len(rows)
        checkpoint["updated_at"] = _utc_now()
        _write_json_atomic(checkpoint_path, checkpoint)
        _log(
            "Dependencies: {}/{} packages, {} edges".format(
                batch_end,
                total,
                checkpoint["dependency_rows"],
            )
        )


def _manifest_from_checkpoint(
    checkpoint,
    batch_size,
    generation_relative_path,
    file_integrity,
):
    dependency_enabled = bool(checkpoint.get("dependencies_enabled"))
    generation_id = str(checkpoint.get("generation_id") or "")
    asset_relative_path = generation_relative_path + "/" + ASSET_OUTPUT_NAME
    dependency_relative_path = generation_relative_path + "/" + DEPENDENCY_OUTPUT_NAME
    checkpoint_relative_path = generation_relative_path + "/" + CHECKPOINT_OUTPUT_NAME
    warnings = []
    if not dependency_enabled:
        warnings.append(
            "Dependency export was disabled by BTC_KB_REGISTRY_DEPENDENCIES=0"
        )
    if checkpoint.get("errors"):
        warnings.append(
            "{} bounded dependency query errors were recorded in the checkpoint".format(
                len(checkpoint.get("errors") or [])
            )
        )
    unresolved_count = int(
        (checkpoint.get("dependency_counts") or {}).get(
            UNRESOLVED_DEPENDENCY_TYPE,
            0,
        )
        or 0
    )
    if unresolved_count:
        warnings.append(
            "{} dependency targets were identifiers rather than package paths; "
            "they are exported as explicit unresolved records".format(unresolved_count)
        )
    generated_at = _utc_now()
    files = {
        "assets": {
            "path": asset_relative_path,
            "sha256": file_integrity["assets"]["sha256"],
            "bytes": int(file_integrity["assets"]["bytes"]),
            "lines": int(file_integrity["assets"]["lines"]),
            "record_count": int(checkpoint.get("asset_rows") or 0),
            "row_schema": ASSET_ROW_SCHEMA,
        },
        "dependencies": {
            "path": dependency_relative_path,
            "sha256": file_integrity["dependencies"]["sha256"],
            "bytes": int(file_integrity["dependencies"]["bytes"]),
            "lines": int(file_integrity["dependencies"]["lines"]),
            "record_count": int(checkpoint.get("dependency_rows") or 0),
            "row_schema": DEPENDENCY_ROW_SCHEMA,
        },
        "checkpoint": {
            "path": checkpoint_relative_path,
            "sha256": file_integrity["checkpoint"]["sha256"],
            "bytes": int(file_integrity["checkpoint"]["bytes"]),
            "schema": CHECKPOINT_SCHEMA,
        },
    }
    return {
        "schema": SCHEMA_VERSION,
        "status": str(checkpoint.get("status") or "UNKNOWN"),
        "generation_id": generation_id,
        "generated_at": generated_at,
        "generated_at_utc": generated_at,
        "producer": {
            "script": os.path.basename(__file__),
            "source_sha256": str(checkpoint.get("producer_source_sha256") or ""),
        },
        "publication": {
            "mode": "immutable_generation_manifest_commit",
            "commit_file": MANIFEST_OUTPUT_NAME,
            "generation_path": generation_relative_path,
        },
        "source": {
            "kind": "unreal_asset_registry",
            "api": "AssetRegistryHelpers.get_asset_registry",
            "engine_version": _engine_version(),
            "on_disk_assets_only": True,
        },
        "inventory_signature": checkpoint.get("inventory_signature") or "",
        "asset_count": int(checkpoint.get("asset_rows") or 0),
        "package_count": int(checkpoint.get("package_total") or 0),
        "dependency_count": int(checkpoint.get("dependency_rows") or 0),
        "dependency_counts": checkpoint.get("dependency_counts") or {},
        "dependencies_enabled": dependency_enabled,
        "tag_allowlist": list(TAG_ALLOWLIST),
        "dependency_types": [
            *(dependency_type for dependency_type, _option_name in DEPENDENCY_TYPES),
            UNRESOLVED_DEPENDENCY_TYPE,
        ],
        "checkpoint": {
            "schema": CHECKPOINT_SCHEMA,
            "status": str(checkpoint.get("status") or "UNKNOWN"),
            "generation_id": generation_id,
            "batch_size": int(batch_size),
            "resumable": True,
            "phase": checkpoint.get("phase") or "UNKNOWN",
        },
        "outputs": {
            "assets": asset_relative_path,
            "dependencies": dependency_relative_path,
            "checkpoint": checkpoint_relative_path,
        },
        "files": files,
        "output_integrity": {
            "assets_sha256": file_integrity["assets"]["sha256"],
            "assets_bytes": int(file_integrity["assets"]["bytes"]),
            "assets_lines": int(file_integrity["assets"]["lines"]),
            "dependencies_sha256": file_integrity["dependencies"]["sha256"],
            "dependencies_bytes": int(file_integrity["dependencies"]["bytes"]),
            "dependencies_lines": int(file_integrity["dependencies"]["lines"]),
            "checkpoint_sha256": file_integrity["checkpoint"]["sha256"],
            "checkpoint_bytes": int(file_integrity["checkpoint"]["bytes"]),
        },
        "skipped_assets_without_identity": int(
            checkpoint.get("skipped_assets_without_identity") or 0
        ),
        "duplicate_asset_identities": int(
            checkpoint.get("duplicate_asset_identities") or 0
        ),
        "warnings": warnings,
    }


def export_registry_snapshot():
    if unreal is None:
        raise RuntimeError("This exporter must run inside ARK DevKit Unreal Python")

    output_dir = _resolve_output_dir()
    dependency_enabled = _env_bool(
        "BTC_KB_REGISTRY_DEPENDENCIES",
        True,
    )
    batch_size = _env_batch_size()
    producer_source_sha256 = _sha256_file(os.path.abspath(__file__))

    manifest_path = os.path.join(output_dir, MANIFEST_OUTPUT_NAME)
    staging_dir = os.path.join(output_dir, STAGING_DIRECTORY_NAME)
    asset_path = os.path.join(staging_dir, ASSET_OUTPUT_NAME)
    dependency_path = os.path.join(
        staging_dir,
        DEPENDENCY_OUTPUT_NAME,
    )
    checkpoint_path = os.path.join(staging_dir, CHECKPOINT_OUTPUT_NAME)

    registry, asset_entries, skipped, duplicates = _get_registry_assets()
    package_names = sorted(
        set(
            package_name
            for package_name in (
                _asset_package_name(asset_data) for _key, asset_data in asset_entries
            )
            if package_name
        )
    )
    _log(
        "Registry ready: {} assets, {} packages".format(
            len(asset_entries),
            len(package_names),
        )
    )
    inventory_signature = _inventory_signature(asset_entries, batch_size)
    _log("Inventory signature: {}".format(inventory_signature))

    published_manifest = _published_manifest_if_current(
        output_dir,
        inventory_signature,
        dependency_enabled,
        len(asset_entries),
        len(package_names),
    )
    if published_manifest is not None:
        _log(
            "Published generation {} is current and verified".format(
                published_manifest.get("generation_id")
            )
        )
        return published_manifest

    if not os.path.isdir(staging_dir):
        os.makedirs(staging_dir)
    checkpoint = _load_checkpoint(checkpoint_path)
    resumable = _checkpoint_is_resumable(
        checkpoint,
        producer_source_sha256,
        inventory_signature,
        dependency_enabled,
        len(asset_entries),
        len(package_names),
        asset_path,
        dependency_path,
    )
    if resumable:
        _truncate_file(
            asset_path,
            int(checkpoint.get("asset_output_bytes") or 0),
        )
        _truncate_file(
            dependency_path,
            int(checkpoint.get("dependency_output_bytes") or 0),
        )
        checkpoint["batch_size"] = batch_size
        checkpoint["updated_at"] = _utc_now()
        _write_json_atomic(checkpoint_path, checkpoint)
        _log(
            "Resuming phase={} asset_cursor={} package_cursor={}".format(
                checkpoint.get("phase"),
                checkpoint.get("asset_cursor"),
                checkpoint.get("package_cursor"),
            )
        )
    else:
        _truncate_file(asset_path, 0)
        _truncate_file(dependency_path, 0)
        checkpoint = _new_checkpoint(
            producer_source_sha256,
            inventory_signature,
            dependency_enabled,
            batch_size,
            len(asset_entries),
            len(package_names),
            skipped,
            duplicates,
        )
        _write_json_atomic(checkpoint_path, checkpoint)
        _log("Started a new checkpointed snapshot")

    if checkpoint.get("phase") == "assets":
        _write_asset_rows(
            asset_entries,
            asset_path,
            checkpoint_path,
            checkpoint,
            batch_size,
        )
        checkpoint["phase"] = "dependencies" if dependency_enabled else "complete"
        checkpoint["updated_at"] = _utc_now()
        _write_json_atomic(checkpoint_path, checkpoint)

    if dependency_enabled and checkpoint.get("phase") == "dependencies":
        _write_dependency_rows(
            registry,
            package_names,
            dependency_path,
            checkpoint_path,
            checkpoint,
            batch_size,
        )
        checkpoint["phase"] = "complete"
        checkpoint["updated_at"] = _utc_now()
        _write_json_atomic(checkpoint_path, checkpoint)

    if checkpoint.get("phase") != "complete":
        raise RuntimeError(
            "Unexpected checkpoint phase {}".format(checkpoint.get("phase"))
        )

    # Hash, byte, and line counts are all computed before anything becomes
    # visible through the root publication manifest.
    asset_integrity = _file_integrity(asset_path)
    dependency_integrity = _file_integrity(dependency_path)
    if asset_integrity["lines"] != int(checkpoint.get("asset_rows") or 0):
        raise RuntimeError("Asset row count does not match committed JSONL line count")
    if dependency_integrity["lines"] != int(checkpoint.get("dependency_rows") or 0):
        raise RuntimeError(
            "Dependency row count does not match committed JSONL line count"
        )

    checkpoint["asset_output_bytes"] = asset_integrity["bytes"]
    checkpoint["asset_output_lines"] = asset_integrity["lines"]
    checkpoint["dependency_output_bytes"] = dependency_integrity["bytes"]
    checkpoint["dependency_output_lines"] = dependency_integrity["lines"]
    checkpoint["asset_output_sha256"] = asset_integrity["sha256"]
    checkpoint["dependency_output_sha256"] = dependency_integrity["sha256"]
    checkpoint["status"] = (
        "COMPLETE_WITH_WARNINGS" if checkpoint.get("errors") else "COMPLETE"
    )
    checkpoint["completed_at"] = checkpoint.get("completed_at") or _utc_now()
    checkpoint["updated_at"] = _utc_now()
    _write_json_atomic(checkpoint_path, checkpoint)

    for path in (
        asset_path,
        dependency_path,
        checkpoint_path,
    ):
        _assert_sanitized_file(path)

    generation_id = str(checkpoint.get("generation_id") or "")
    if not _valid_generation_id(generation_id):
        raise RuntimeError("Checkpoint generation_id is invalid")
    generation_relative_path = GENERATIONS_DIRECTORY_NAME + "/" + generation_id
    generations_dir = os.path.join(
        output_dir,
        GENERATIONS_DIRECTORY_NAME,
    )
    if not os.path.isdir(generations_dir):
        os.makedirs(generations_dir)
    generation_dir = os.path.join(generations_dir, generation_id)
    if os.path.exists(generation_dir):
        raise RuntimeError(
            "Generation destination already exists: {}".format(generation_id)
        )

    checkpoint_integrity = _file_integrity(checkpoint_path)
    file_integrity = {
        "assets": asset_integrity,
        "dependencies": dependency_integrity,
        "checkpoint": checkpoint_integrity,
    }
    manifest = _manifest_from_checkpoint(
        checkpoint,
        batch_size,
        generation_relative_path,
        file_integrity,
    )
    manifest_staging_path = os.path.join(staging_dir, MANIFEST_STAGING_NAME)
    _write_json_atomic(manifest_staging_path, manifest)
    _assert_sanitized_file(manifest_staging_path)

    # Same-volume directory rename publishes the immutable data set.  The
    # manifest is replaced only after that succeeds, and therefore acts as the
    # single atomic commit point visible to readers.
    os.replace(staging_dir, generation_dir)
    _fsync_directory(generations_dir)
    verified_manifest_path = os.path.join(
        generation_dir,
        MANIFEST_STAGING_NAME,
    )
    os.replace(verified_manifest_path, manifest_path)
    _fsync_directory(output_dir)

    _log(
        "Completed generation {}: {} assets, {} dependency edges".format(
            generation_id,
            checkpoint.get("asset_rows"),
            checkpoint.get("dependency_rows"),
        )
    )
    return manifest


if unreal is not None or __name__ == "__main__":
    try:
        EXPORT_RESULT = export_registry_snapshot()
    except Exception as exc:
        safe_message = _sanitize_text(exc)
        _log("FAILED: {}".format(safe_message))
        raise RuntimeError(safe_message)
