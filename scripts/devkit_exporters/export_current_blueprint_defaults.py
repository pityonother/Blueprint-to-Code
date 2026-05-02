r"""Export defaults from the current ARK DevKit / Unreal Blueprint.

Run this inside ARK DevKit's Python Console after opening or selecting a
Blueprint asset:

exec(open(r"C:\Users\ac\Documents\project gaming\Blueprint to Code\scripts\devkit_exporters\export_current_blueprint_defaults.py", encoding="utf-8").read())

The script writes defaults.json, components.json, and diagnostic reports under
captures/<BlueprintName>/ so the local Blueprint translator can consume them.
"""

from __future__ import print_function

import datetime
import json
import os
import re
import traceback

try:
    import unreal
except Exception:
    unreal = None


PROJECT_ROOT = r"C:\Users\ac\Documents\project gaming\Blueprint to Code"
CAPTURE_ROOT = os.path.join(PROJECT_ROOT, "captures")
REQUEST_PATH = os.path.join(CAPTURE_ROOT, "_devkit_export_request.json")

# Optional hard-coded fallback. Most users should leave this blank and paste the
# asset path into the GUI prompt or scripts/devkit_exporters/devkit_export_path_gui.py.
ASSET_PATH = ""

INCLUDE_INHERITED_CLASS_DEFAULTS = True
INCLUDE_COMPONENT_DEFAULTS = True
SAFE_COMPONENT_EXPORT = True
ENABLE_UNSAFE_COMPONENT_REFLECTION = False
MAX_CLASS_DEFAULT_PROPERTIES = 1200
MAX_COMPONENT_PROPERTIES = 700
MAX_COLLECTION_ITEMS = 5000
MAX_SERIALIZE_DEPTH = 5


class ExportState(object):
    def __init__(self):
        self.warnings = []
        self.errors = []
        self.skipped = []
        self._skip_index = {}
        self.skipped_attempts = 0
        self.debug = []

    def info(self, message):
        self.debug.append(str(message))
        log("INFO: " + str(message))

    def warn(self, message):
        self.warnings.append(str(message))
        log("WARN: " + str(message))

    def error(self, message):
        self.errors.append(str(message))
        log("ERROR: " + str(message))

    def skip(self, where, name, reason):
        self.skipped_attempts += 1
        marker = (str(where), str(name), str(reason))
        if marker in self._skip_index:
            self.skipped[self._skip_index[marker]]["count"] += 1
            return
        self._skip_index[marker] = len(self.skipped)
        self.skipped.append({"where": marker[0], "name": marker[1], "reason": marker[2], "count": 1})


STATE = ExportState()


def log(message):
    text = "[BlueprintDefaultsExport] " + str(message)
    print(text)
    if unreal is not None:
        try:
            unreal.log(text)
        except Exception:
            pass


def safe_filename(value, fallback="Blueprint"):
    text = str(value or fallback).strip()
    text = re.sub(r"[^\w.\- ]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text)
    return text.strip("._ ") or fallback


def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def write_json(path, payload):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")


def write_text(path, text):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8-sig") as handle:
        handle.write(text)


def object_name(obj):
    if obj is None:
        return ""
    for attr in ("get_name", "get_fname"):
        func = getattr(obj, attr, None)
        if callable(func):
            try:
                return str(func())
            except Exception:
                pass
    try:
        return str(obj)
    except Exception:
        return ""


def blueprint_asset_name(obj):
    name = object_name(obj)
    if name.endswith("_C"):
        return name[:-2]
    return name


def object_path(obj):
    if obj is None:
        return ""
    for attr in ("get_path_name", "get_full_name"):
        func = getattr(obj, attr, None)
        if callable(func):
            try:
                return str(func())
            except Exception:
                pass
    try:
        return str(obj)
    except Exception:
        return ""


def class_name(obj):
    if obj is None:
        return ""
    try:
        cls = obj.get_class() if hasattr(obj, "get_class") else obj
        return object_name(cls).split(".")[-1]
    except Exception:
        return type(obj).__name__


def get_prop(obj, name, default=None):
    if obj is None:
        return default
    try:
        return obj.get_editor_property(name)
    except Exception:
        pass
    try:
        return getattr(obj, name)
    except Exception:
        return default


def call_any(obj, names):
    for name in names:
        func = getattr(obj, name, None)
        if callable(func):
            try:
                return func()
            except Exception:
                pass
    return None


def is_unreal_object(value):
    return value is not None and (
        hasattr(value, "get_path_name")
        or hasattr(value, "get_class")
        or str(type(value)).find("unreal.") >= 0
    )


def is_callable_value(value):
    if callable(value):
        return True
    type_name = type(value).__name__.lower()
    return "function" in type_name or "method" in type_name


def serialize_object_reference(value):
    return {
        "name": object_name(value),
        "path": object_path(value),
        "class": class_name(value),
    }


def serialize_struct_like(value, depth, visited):
    type_name = type(value).__name__
    lowered = type_name.lower()
    field_sets = [
        ("vector", ("x", "y", "z")),
        ("vector2d", ("x", "y")),
        ("rotator", ("pitch", "yaw", "roll")),
        ("quat", ("x", "y", "z", "w")),
        ("linearcolor", ("r", "g", "b", "a")),
        ("color", ("r", "g", "b", "a")),
        ("transform", ("translation", "rotation", "scale3d")),
    ]
    for marker, fields in field_sets:
        if marker in lowered:
            result = {"__type": type_name}
            matched = False
            for field in fields:
                if hasattr(value, field):
                    matched = True
                    result[field] = serialize_value(getattr(value, field), depth + 1, visited)
            if matched:
                return result
    return None


def serialize_value(value, depth=0, visited=None):
    if visited is None:
        visited = set()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if depth >= MAX_SERIALIZE_DEPTH:
        return {"__truncated": True, "type": type(value).__name__, "value": str(value)}

    value_id = id(value)
    if value_id in visited:
        return {"__cycle": True, "type": type(value).__name__}
    visited.add(value_id)

    try:
        struct_payload = serialize_struct_like(value, depth, visited)
        if struct_payload is not None:
            return struct_payload

        if isinstance(value, (list, tuple, set)):
            items = []
            for index, item in enumerate(list(value)):
                if index >= MAX_COLLECTION_ITEMS:
                    items.append({"__truncated_items": len(value) - MAX_COLLECTION_ITEMS})
                    break
                items.append(serialize_value(item, depth + 1, visited))
            return items

        if isinstance(value, dict):
            result = {}
            for index, key in enumerate(value):
                if index >= MAX_COLLECTION_ITEMS:
                    result["__truncated_items"] = len(value) - MAX_COLLECTION_ITEMS
                    break
                result[str(key)] = serialize_value(value[key], depth + 1, visited)
            return result

        if is_unreal_object(value):
            return serialize_object_reference(value)

        # Unreal containers are often iterable even when they are not Python lists.
        if not isinstance(value, (bytes, bytearray)) and hasattr(value, "__iter__"):
            items = []
            for index, item in enumerate(value):
                if index >= MAX_COLLECTION_ITEMS:
                    items.append({"__truncated_items": index - MAX_COLLECTION_ITEMS + 1})
                    break
                items.append(serialize_value(item, depth + 1, visited))
            return items

        return str(value)
    except Exception as exc:
        return {"__serialize_error": str(exc), "type": type(value).__name__, "value": str(value)}
    finally:
        try:
            visited.remove(value_id)
        except Exception:
            pass


def default_entry(value, source, category=""):
    return {
        "default": serialize_value(value),
        "type": type(value).__name__,
        "category": category,
        "source": source,
    }


def normalize_asset_path(raw_text):
    """Accept copied UE references and return an EditorAssetLibrary path."""
    text = str(raw_text or "").strip()
    if not text:
        return ""
    text = text.replace("\\", "/").strip()
    text = text.strip("\"'")

    # Handles: Blueprint'/Game/Foo/BP.BP'
    quoted = re.search(r"['\"](?P<path>/Game/[^'\"]+)['\"]", text)
    if quoted:
        text = quoted.group("path").strip()

    # Handles lines copied with labels, for example: ObjectPath=/Game/Foo/BP.BP
    path_match = re.search(r"(?P<path>/Game/[^\s,'\"]+)", text)
    if path_match:
        text = path_match.group("path").strip()

    text = text.strip("\"'")
    if not text.startswith("/Game/"):
        return ""

    # Convert generated class references back to the Blueprint asset when possible:
    # /Game/Foo/MyBP.MyBP_C -> /Game/Foo/MyBP.MyBP
    if "." in text and text.endswith("_C"):
        package, obj = text.rsplit(".", 1)
        text = package + "." + obj[:-2]

    # If only the package path was pasted, add the object name:
    # /Game/Foo/MyBP -> /Game/Foo/MyBP.MyBP
    if "." not in text:
        object_name_part = text.rsplit("/", 1)[-1]
        if object_name_part:
            text = text + "." + object_name_part

    return text


def read_request_asset_path():
    if not os.path.isfile(REQUEST_PATH):
        return ""
    try:
        with open(REQUEST_PATH, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return normalize_asset_path(data.get("asset_path") or data.get("path") or "")
    except Exception as exc:
        STATE.warn("Could not read {}: {}".format(REQUEST_PATH, exc))
    return ""


def read_request_payload():
    if not os.path.isfile(REQUEST_PATH):
        return {}
    try:
        with open(REQUEST_PATH, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        return {"read_error": str(exc)}


def clipboard_text():
    if unreal is None:
        return ""
    system_library = getattr(unreal, "SystemLibrary", None)
    if system_library is not None:
        paste = getattr(system_library, "clipboard_paste", None)
        if callable(paste):
            try:
                return str(paste() or "")
            except Exception:
                pass
    return ""


def prompt_asset_path_gui(default_text=""):
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception as exc:
        STATE.warn("Tkinter GUI is not available in this DevKit Python: {}".format(exc))
        return ""

    result = {"value": ""}
    root = tk.Tk()
    root.title("Blueprint Defaults Export")
    root.geometry("760x230")
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    label_text = (
        "Paste the Blueprint reference or Object Path copied from ARK DevKit.\n"
        "Examples: Blueprint'/Game/Mods/MyMod/MyBP.MyBP' or /Game/Mods/MyMod/MyBP.MyBP"
    )
    tk.Label(root, text=label_text, justify="left", anchor="w").pack(fill="x", padx=12, pady=(12, 8))
    text_box = tk.Text(root, height=4, wrap="word")
    text_box.pack(fill="both", expand=True, padx=12)
    if default_text:
        text_box.insert("1.0", default_text)

    def submit():
        value = normalize_asset_path(text_box.get("1.0", "end").strip())
        if not value:
            messagebox.showwarning("Invalid path", "Please paste a path starting with /Game/.")
            return
        result["value"] = value
        root.destroy()

    def cancel():
        root.destroy()

    buttons = tk.Frame(root)
    buttons.pack(fill="x", padx=12, pady=12)
    tk.Button(buttons, text="Export This Blueprint", command=submit, width=22).pack(side="left")
    tk.Button(buttons, text="Cancel", command=cancel, width=12).pack(side="right")
    root.bind("<Control-Return>", lambda _event: submit())
    root.bind("<Escape>", lambda _event: cancel())
    try:
        text_box.focus_set()
        root.mainloop()
    except Exception as exc:
        STATE.warn("GUI prompt failed: {}".format(exc))
        return ""
    return result["value"]


def load_asset(path):
    path = normalize_asset_path(path)
    if not path or unreal is None:
        return None
    library = getattr(unreal, "EditorAssetLibrary", None)
    if library is not None and hasattr(library, "load_asset"):
        try:
            return library.load_asset(path)
        except Exception as exc:
            STATE.warn("Failed to load ASSET_PATH '{}': {}".format(path, exc))
    return None


def generated_class_path_candidates(path):
    normalized = normalize_asset_path(path)
    if not normalized:
        return []
    candidates = []
    if "." in normalized:
        package, obj = normalized.rsplit(".", 1)
        candidates.append(package + "." + obj + "_C")
        candidates.append(package + "_C." + obj + "_C")
    else:
        obj = normalized.rsplit("/", 1)[-1]
        if obj:
            candidates.append(normalized + "." + obj + "_C")
            candidates.append(normalized + "_C." + obj + "_C")
    return list(dict.fromkeys(candidates))


def load_blueprint_class(path):
    path = normalize_asset_path(path)
    if not path or unreal is None:
        return None
    library = getattr(unreal, "EditorAssetLibrary", None)
    if library is not None:
        method = getattr(library, "load_blueprint_class", None)
        if callable(method):
            try:
                loaded = method(path)
                if loaded:
                    STATE.info("Loaded generated class through EditorAssetLibrary.load_blueprint_class: {}".format(object_name(loaded)))
                    return loaded
            except Exception as exc:
                STATE.warn("EditorAssetLibrary.load_blueprint_class failed for '{}': {}".format(path, exc))

    load_object = getattr(unreal, "load_object", None)
    if callable(load_object):
        for candidate in generated_class_path_candidates(path):
            try:
                loaded = load_object(None, candidate)
                if loaded:
                    STATE.info("Loaded generated class through unreal.load_object: {}".format(candidate))
                    return loaded
            except Exception as exc:
                STATE.skip("generated_class_load", candidate, str(exc))

    load_class = getattr(unreal, "load_class", None)
    if callable(load_class):
        for candidate in generated_class_path_candidates(path):
            try:
                loaded = load_class(None, candidate)
                if loaded:
                    STATE.info("Loaded generated class through unreal.load_class: {}".format(candidate))
                    return loaded
            except Exception as exc:
                STATE.skip("generated_class_load", candidate, str(exc))
    return None


def blueprint_generated_class(asset):
    generated = get_prop(asset, "generated_class")
    if generated:
        return generated
    generated = get_prop(asset, "GeneratedClass")
    if generated:
        return generated
    if "BlueprintGeneratedClass" in class_name(asset):
        return asset
    return None


def is_blueprint(asset):
    if asset is None:
        return False
    if blueprint_generated_class(asset):
        return True
    name = class_name(asset).lower()
    return "blueprint" in name and "library" not in name


def selected_assets():
    assets = []
    if unreal is None:
        return assets
    utility = getattr(unreal, "EditorUtilityLibrary", None)
    if utility is not None:
        for method_name in ("get_selected_assets",):
            method = getattr(utility, method_name, None)
            if callable(method):
                try:
                    assets.extend(method() or [])
                except Exception:
                    pass
    return assets


def edited_assets():
    assets = []
    if unreal is None:
        return assets
    subsystem_class = getattr(unreal, "AssetEditorSubsystem", None)
    getter = getattr(unreal, "get_editor_subsystem", None)
    if subsystem_class is not None and callable(getter):
        try:
            subsystem = getter(subsystem_class)
            if subsystem is not None:
                for method_name in ("get_all_edited_assets", "get_open_assets"):
                    method = getattr(subsystem, method_name, None)
                    if callable(method):
                        assets.extend(method() or [])
        except Exception:
            pass
    return assets


def find_current_blueprint():
    explicit = load_asset(ASSET_PATH)
    if is_blueprint(explicit):
        return explicit, "ASSET_PATH"

    for asset in edited_assets():
        if is_blueprint(asset):
            return asset, "open_asset_editor"

    for asset in selected_assets():
        if is_blueprint(asset):
            return asset, "content_browser_selection"

    for path, source in (
        (read_request_asset_path(), "request_file"),
        (clipboard_text(), "clipboard"),
    ):
        explicit = load_asset(path)
        if is_blueprint(explicit):
            return explicit, source

    pasted_path = prompt_asset_path_gui(clipboard_text())
    explicit = load_asset(pasted_path)
    if is_blueprint(explicit):
        return explicit, "gui_prompt"

    return None, ""


def get_cdo(generated_class):
    if generated_class is None:
        return None
    for prop_name in ("class_default_object", "ClassDefaultObject", "default_object", "DefaultObject"):
        value = get_prop(generated_class, prop_name)
        if value is not None and not is_callable_value(value):
            STATE.info("Got CDO from generated class property: {}".format(prop_name))
            return value
    for attr in ("get_default_object",):
        func = getattr(generated_class, attr, None)
        if callable(func):
            try:
                value = func()
                if value is not None:
                    STATE.info("Got CDO from generated_class.{}()".format(attr))
                    return value
            except Exception:
                pass
    for getter_name in ("get_default_object", "get_mutable_default_object"):
        getter = getattr(unreal, getter_name, None) if unreal is not None else None
        if callable(getter):
            try:
                value = getter(generated_class)
                if value is not None:
                    STATE.info("Got CDO from unreal.{}()".format(getter_name))
                    return value
            except Exception as exc:
                STATE.skip("get_cdo", getter_name, str(exc))
    return None


def parent_class_name(blueprint, generated_class):
    parent = get_prop(blueprint, "parent_class") or get_prop(blueprint, "ParentClass")
    if not parent and generated_class is not None:
        parent = call_any(generated_class, ("get_super_class", "get_super_struct"))
    return object_name(parent)


def blueprint_variable_names(blueprint):
    names = []
    library = getattr(unreal, "BlueprintEditorLibrary", None) if unreal is not None else None
    if library is not None:
        method = getattr(library, "get_blueprint_variable_names", None)
        if callable(method):
            try:
                names = [str(item) for item in (method(blueprint) or [])]
            except Exception as exc:
                STATE.warn("BlueprintEditorLibrary.get_blueprint_variable_names failed: {}".format(exc))
    if names:
        return sorted(set(names))

    # Older DevKit builds may not expose BlueprintEditorLibrary. Fall back to
    # NewVariables metadata where available, then to CDO editor properties.
    for prop_name in ("new_variables", "NewVariables"):
        raw = get_prop(blueprint, prop_name)
        if raw:
            for item in raw:
                name = get_prop(item, "var_name") or get_prop(item, "VarName") or get_prop(item, "friendly_name")
                if name:
                    names.append(str(name))
    return sorted(set(names))


def blueprint_variable_default(blueprint, cdo, name):
    library = getattr(unreal, "BlueprintEditorLibrary", None) if unreal is not None else None
    if library is not None:
        method = getattr(library, "get_blueprint_variable_default_value", None)
        if callable(method):
            try:
                value = method(blueprint, name)
                if value is not None:
                    return value, "BlueprintEditorLibrary"
            except Exception:
                pass
    value = get_prop(cdo, name)
    if value is not None:
        return value, "class_default_object"
    return None, ""


def valid_property_name(name):
    if not name or name.startswith("_"):
        return False
    if name in ("__class__", "__doc__", "__module__", "None"):
        return False
    lowered = name.lower()
    if lowered.startswith(("get_", "set_", "call_", "static_")):
        return False
    if lowered in {
        "outer",
        "class",
        "object",
        "package",
        "world",
        "transient_package",
        "simple_construction_script",
        "uber_graph_frame",
    }:
        return False
    return True


def property_name_from_property(prop):
    for attr in ("get_name", "get_fname"):
        func = getattr(prop, attr, None)
        if callable(func):
            try:
                return str(func())
            except Exception:
                pass
    for attr in ("name", "Name"):
        value = getattr(prop, attr, None)
        if value:
            return str(value)
    return ""


def editor_property_names(obj):
    names = set()
    if obj is None:
        return []

    method = getattr(obj, "get_editor_property_names", None)
    if callable(method):
        try:
            names.update(str(item) for item in (method() or []))
        except Exception:
            pass

    cls = None
    try:
        cls = obj.get_class()
    except Exception:
        cls = None
    if cls is not None:
        for method_name in ("get_properties", "properties"):
            method = getattr(cls, method_name, None)
            if callable(method):
                try:
                    for prop in method() or []:
                        name = property_name_from_property(prop)
                        if name:
                            names.add(name)
                except Exception:
                    pass

    # Fallback: in many Unreal Python builds reflection-backed editor properties
    # appear in dir(obj), even when UClass property enumeration is limited.
    try:
        for name in dir(obj):
            if valid_property_name(name):
                names.add(name)
    except Exception:
        pass

    return sorted(name for name in names if valid_property_name(name))


def wc_property_names(obj):
    method = getattr(obj, "wc_get_all_property_names", None)
    if not callable(method):
        return []
    try:
        names = method() or []
        return sorted(str(name) for name in names if valid_property_name(str(name)))
    except Exception as exc:
        STATE.skip("wc_get_all_property_names", object_name(obj), str(exc))
        return []


def wc_all_property_values(obj):
    method = getattr(obj, "wc_get_all_property_values", None)
    if not callable(method):
        return {}
    try:
        raw = method() or {}
    except Exception as exc:
        STATE.skip("wc_get_all_property_values", object_name(obj), str(exc))
        return {}
    if isinstance(raw, dict):
        return {str(key): value for key, value in raw.items() if valid_property_name(str(key))}
    values = {}
    try:
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                key, value = item[0], item[1]
                if valid_property_name(str(key)):
                    values[str(key)] = value
    except Exception:
        pass
    return values


def wc_get_property_value(obj, name):
    method = getattr(obj, "wc_get_property_value", None)
    if not callable(method):
        return False, None
    for candidate in (name, str(name)):
        try:
            return True, method(candidate)
        except Exception:
            continue
    try:
        if unreal is not None and hasattr(unreal, "Name"):
            return True, method(unreal.Name(str(name)))
    except Exception:
        pass
    STATE.skip("wc_get_property_value", name, "failed for string and Name arguments")
    return False, None


def raw_property_value(obj, name):
    ok, value = wc_get_property_value(obj, name)
    if ok:
        return True, value, "wc_get_property_value"
    try:
        return True, obj.get_editor_property(name), "get_editor_property"
    except Exception:
        pass
    try:
        return True, getattr(obj, name), "getattr"
    except Exception:
        return False, None, ""


def extract_property_name_list(value):
    names = []
    if value is None:
        return names
    if isinstance(value, dict):
        candidates = list(value.keys())
    else:
        try:
            candidates = list(value)
        except Exception:
            candidates = []
    for item in candidates:
        if isinstance(item, (list, tuple)) and item:
            item = item[0]
        text = str(item)
        if valid_property_name(text) and text not in names:
            names.append(text)
    return names


def blueprint_property_guid_names(blueprint, generated_class, cdo):
    names = []
    for obj, label in (
        (generated_class, "generated_class"),
        (blueprint, "blueprint_asset"),
        (cdo, "class_default_object"),
    ):
        if obj is None:
            continue
        ok, value, method = raw_property_value(obj, "PropertyGuids")
        if not ok:
            continue
        found = extract_property_name_list(value)
        if found:
            STATE.info("Found {} Blueprint property names from {}.PropertyGuids via {}".format(len(found), label, method))
            for name in found:
                if name not in names:
                    names.append(name)
    return names


def collect_named_defaults(names, objects, source):
    defaults = {}
    missing = []
    for name in names:
        found = False
        for obj, label in objects:
            if obj is None:
                continue
            ok, value, method = raw_property_value(obj, name)
            if not ok:
                continue
            if is_callable_value(value):
                STATE.skip(source, name, "callable/editor method from {} via {}".format(label, method))
                found = True
                break
            defaults[name] = default_entry(value, source + ":" + label)
            defaults[name]["_read_method"] = method
            found = True
            break
        if not found:
            missing.append(name)
            defaults[name] = {
                "default": None,
                "type": "unknown",
                "category": "",
                "source": source + ":name_only",
                "_warning": "Found the Blueprint property name in PropertyGuids, but this DevKit Python build did not return its default value.",
            }
    if missing:
        STATE.warn("Found {} Blueprint property names but could not read values for {} of them.".format(len(names), len(missing)))
        for name in missing[:100]:
            STATE.skip(source, name, "name found in PropertyGuids but value read failed")
    return defaults


def collect_wc_defaults(obj, source, limit, omit_names=None):
    defaults = {}
    if obj is None:
        return defaults
    omit = set(omit_names or [])
    raw_values = wc_all_property_values(obj)
    if raw_values:
        for name, value in sorted(raw_values.items()):
            if name in omit:
                continue
            if len(defaults) >= limit:
                STATE.skip(source, name, "property limit reached")
                continue
            if is_callable_value(value):
                STATE.skip(source, name, "callable/editor method, not a property value")
                continue
            defaults[name] = default_entry(value, source)
        if defaults:
            STATE.info("Collected {} defaults through wc_get_all_property_values from {}".format(len(defaults), object_name(obj)))
            return defaults

    for name in wc_property_names(obj):
        if name in omit:
            continue
        if len(defaults) >= limit:
            STATE.skip(source, name, "property limit reached")
            continue
        ok, value = wc_get_property_value(obj, name)
        if not ok:
            continue
        if is_callable_value(value):
            STATE.skip(source, name, "callable/editor method, not a property value")
            continue
        defaults[name] = default_entry(value, source)
    if defaults:
        STATE.info("Collected {} defaults through wc_get_property_value from {}".format(len(defaults), object_name(obj)))
    return defaults


def collect_object_defaults(obj, source, limit, omit_names=None):
    defaults = {}
    omit = set(omit_names or [])
    count = 0
    for name in editor_property_names(obj):
        if name in omit:
            continue
        if count >= limit:
            STATE.skip(source, name, "property limit reached")
            continue
        try:
            value = get_prop(obj, name, None)
        except Exception as exc:
            STATE.skip(source, name, "read failed: {}".format(exc))
            continue
        if is_callable_value(value):
            STATE.skip(source, name, "callable/editor method, not a property value")
            continue
        try:
            json.dumps(serialize_value(value), ensure_ascii=False)
        except Exception as exc:
            STATE.skip(source, name, "json serialization failed: {}".format(exc))
            continue
        defaults[name] = default_entry(value, source)
        count += 1
    return defaults


def collect_blueprint_variable_defaults(blueprint, cdo, generated_class):
    variables = {}
    for name in blueprint_variable_names(blueprint):
        value, source = blueprint_variable_default(blueprint, cdo, name)
        if not source:
            STATE.skip("blueprint_variable", name, "no default value found on BlueprintEditorLibrary or CDO")
            continue
        variables[name] = default_entry(value, "blueprint_variable", category="")

    if variables:
        return variables

    guid_names = blueprint_property_guid_names(blueprint, generated_class, cdo)
    if guid_names:
        variables = collect_named_defaults(
            guid_names,
            (
                (cdo, "class_default_object"),
                (generated_class, "generated_class"),
                (blueprint, "blueprint_asset"),
            ),
            "blueprint_property_guid",
        )
        if variables:
            return variables

    for obj, source in (
        (generated_class, "wc_blueprint_class_property"),
        (cdo, "wc_class_default_object_property"),
    ):
        variables = collect_wc_defaults(obj, source, MAX_CLASS_DEFAULT_PROPERTIES)
        if variables:
            STATE.warn("No Blueprint variable list API was available; using ARK DevKit wc_* property export.")
            return variables

    # Last-resort fallback for older builds: use CDO editor properties as
    # variable candidates. These may include inherited properties, so the report
    # clearly marks the fallback source.
    STATE.warn("No Blueprint variable list API was available; using CDO editor properties as variable candidates.")
    for name, entry in collect_object_defaults(cdo, "class_default_object_fallback", MAX_CLASS_DEFAULT_PROPERTIES).items():
        variables[name] = entry
    return variables


def collect_class_defaults(cdo, blueprint_variable_keys, generated_class=None):
    if not INCLUDE_INHERITED_CLASS_DEFAULTS or cdo is None:
        return {}
    wc_defaults = collect_wc_defaults(cdo, "wc_class_default", MAX_CLASS_DEFAULT_PROPERTIES, omit_names=set(blueprint_variable_keys))
    if wc_defaults:
        return wc_defaults
    if generated_class is not None:
        wc_defaults = collect_wc_defaults(generated_class, "wc_generated_class_default", MAX_CLASS_DEFAULT_PROPERTIES, omit_names=set(blueprint_variable_keys))
        if wc_defaults:
            return wc_defaults
    return collect_object_defaults(
        cdo,
        "class_default",
        MAX_CLASS_DEFAULT_PROPERTIES,
        omit_names=set(blueprint_variable_keys),
    )


def component_template_name(component):
    for prop_name in ("variable_name", "VariableName", "component_name", "ComponentName"):
        ok, value, _method = raw_property_value(component, prop_name)
        if not ok:
            value = get_prop(component, prop_name)
        if value:
            return str(value)
    return object_name(component)


def component_class_hint(name):
    lowered = str(name or "").lower()
    if "movement" in lowered:
        return "CharacterMovementComponent"
    if "status" in lowered:
        return "PrimalCharacterStatusComponent"
    if "audio" in lowered:
        return "AudioComponent"
    if "camera" in lowered:
        return "CameraComponent"
    if "fx" in lowered or "trail" in lowered or "_ns" in lowered or "visual" in lowered:
        return "Niagara/Particle/SceneComponent"
    if "mesh" in lowered:
        return "MeshComponent"
    return ""


def is_component_like_object(value):
    if value is None:
        return False
    text = (class_name(value) + " " + object_name(value)).lower()
    return "component" in text or any(term in text for term in ("audio", "trail", "fx", "camera", "mesh"))


def iter_unreal_collection(value, limit=400):
    if value is None or isinstance(value, (str, bytes, bytearray)):
        return []
    if isinstance(value, dict):
        return list(value.values())[:limit]
    try:
        return list(value)[:limit]
    except Exception:
        return []


def add_component(components_by_key, component, source):
    if component is None:
        return
    name = component_template_name(component)
    key = object_path(component) or name
    if key in components_by_key:
        return
    defaults = {}
    if not SAFE_COMPONENT_EXPORT:
        defaults = collect_wc_defaults(component, "component_default", MAX_COMPONENT_PROPERTIES)
        if not defaults:
            defaults = collect_object_defaults(component, "component_default", MAX_COMPONENT_PROPERTIES)
    components_by_key[key] = {
        "name": name,
        "class": class_name(component),
        "path": object_path(component),
        "defaults": defaults,
        "purpose": "",
        "source": source,
    }


def collect_component_like_references(root, components_by_key, source, depth=0, visited=None):
    if root is None or depth > 3:
        return
    if visited is None:
        visited = set()
    marker = id(root)
    if marker in visited:
        return
    visited.add(marker)

    if is_component_like_object(root):
        add_component(components_by_key, root, source)

    names = set()
    names.update(wc_property_names(root))
    names.update(editor_property_names(root))
    priority_names = [
        name
        for name in names
        if any(term in name.lower() for term in ("component", "template", "node", "handler", "simpleconstruction", "scs"))
    ]
    for prop_name in priority_names[:120]:
        ok, value, method = raw_property_value(root, prop_name)
        if not ok:
            continue
        if is_component_like_object(value):
            add_component(components_by_key, value, "{}.{} via {}".format(source, prop_name, method))
            continue
        for item in iter_unreal_collection(value):
            if is_component_like_object(item):
                add_component(components_by_key, item, "{}.{} via {}".format(source, prop_name, method))
            elif is_unreal_object(item):
                collect_component_like_references(item, components_by_key, "{}.{}".format(source, prop_name), depth + 1, visited)
        if is_unreal_object(value):
            collect_component_like_references(value, components_by_key, "{}.{}".format(source, prop_name), depth + 1, visited)


def get_object_property_any(objects, prop_names):
    for obj, label in objects:
        if obj is None:
            continue
        for prop_name in prop_names:
            ok, value, method = raw_property_value(obj, prop_name)
            if ok and value is not None:
                return value, "{}.{} via {}".format(label, prop_name, method)
            value = get_prop(obj, prop_name)
            if value is not None:
                return value, "{}.{} via get_prop".format(label, prop_name)
    return None, ""


def collect_scs_components(objects, components_by_key):
    scs, source = get_object_property_any(
        objects,
        ("simple_construction_script", "SimpleConstructionScript", "SimpleConstructionScriptGeneratedClass"),
    )
    if not scs:
        return
    collect_component_like_references(scs, components_by_key, source)
    nodes = []
    for method_name in ("get_all_nodes", "get_root_nodes"):
        method = getattr(scs, method_name, None)
        if callable(method):
            try:
                nodes.extend(method() or [])
            except Exception as exc:
                STATE.skip("simple_construction_script", method_name, str(exc))
    for prop_name in ("all_nodes", "root_nodes"):
        raw = get_prop(scs, prop_name)
        if raw:
            try:
                nodes.extend(raw)
            except Exception:
                pass
    seen_nodes = set()
    for node in nodes:
        marker = id(node)
        if marker in seen_nodes:
            continue
        seen_nodes.add(marker)
        component, component_source = get_object_property_any(
            ((node, "scs_node"),),
            ("component_template", "ComponentTemplate", "template", "Template"),
        )
        add_component(components_by_key, component, component_source or "simple_construction_script")


def collect_blueprint_component_templates(objects, components_by_key):
    for obj, label in objects:
        if obj is None:
            continue
        for prop_name in ("component_templates", "ComponentTemplates", "ComponentClassOverrides", "InheritableComponentHandler", "inheritable_component_handler", "timelines", "Timelines"):
            ok, raw, method = raw_property_value(obj, prop_name)
            if not ok:
                raw = get_prop(obj, prop_name)
                method = "get_prop"
            if not raw:
                continue
            if is_component_like_object(raw):
                add_component(components_by_key, raw, "{}.{} via {}".format(label, prop_name, method))
            collect_component_like_references(raw, components_by_key, "{}.{} via {}".format(label, prop_name, method))
            try:
                for component in iter_unreal_collection(raw):
                    if is_component_like_object(component):
                        add_component(components_by_key, component, "{}.{} via {}".format(label, prop_name, method))
            except Exception as exc:
                STATE.skip(label, prop_name, str(exc))


def collect_cdo_components(cdo, components_by_key):
    if cdo is None:
        return
    for method_name in ("get_components_by_class",):
        method = getattr(cdo, method_name, None)
        if callable(method) and unreal is not None:
            actor_component_class = getattr(unreal, "ActorComponent", None)
            if actor_component_class is not None:
                try:
                    for component in method(actor_component_class) or []:
                        add_component(components_by_key, component, "class_default_object_components")
                except Exception as exc:
                    STATE.skip("class_default_object", method_name, str(exc))
    for prop_name in ("blueprint_created_components", "instance_components"):
        raw = get_prop(cdo, prop_name)
        if raw:
            try:
                for component in raw:
                    add_component(components_by_key, component, prop_name)
            except Exception as exc:
                STATE.skip("class_default_object", prop_name, str(exc))


def safe_iter_scs_nodes(scs):
    nodes = []
    for method_name in ("get_all_nodes", "get_root_nodes"):
        method = getattr(scs, method_name, None)
        if callable(method):
            try:
                nodes.extend(method() or [])
            except Exception as exc:
                STATE.skip("safe_simple_construction_script", method_name, str(exc))
    for prop_name in ("all_nodes", "root_nodes"):
        raw = get_prop(scs, prop_name)
        if raw:
            try:
                nodes.extend(raw)
            except Exception as exc:
                STATE.skip("safe_simple_construction_script", prop_name, str(exc))
    result = []
    seen = set()
    for node in nodes:
        marker = id(node)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(node)
    return result


def add_component_node_placeholder(components_by_key, node, source):
    name = ""
    for prop_name in ("variable_name", "VariableName", "internal_variable_name", "InternalVariableName"):
        value = get_prop(node, prop_name)
        if value:
            name = str(value)
            break
    if not name:
        name = object_name(node)
    if not name:
        return
    key = source + ":" + name.lower()
    if key in components_by_key:
        return
    components_by_key[key] = {
        "name": name,
        "class": "",
        "path": object_path(node),
        "defaults": {},
        "purpose": "",
        "source": source,
        "_todo": "SCS node was found, but its component template was not safely readable. Confirm class/defaults in ARK DevKit.",
    }


def collect_scs_components_safe(objects, components_by_key):
    scs, source = get_object_property_any(
        objects,
        ("simple_construction_script", "SimpleConstructionScript"),
    )
    if not scs:
        return
    count_before = len(components_by_key)
    for node in safe_iter_scs_nodes(scs):
        component, component_source = get_object_property_any(
            ((node, "scs_node"),),
            ("component_template", "ComponentTemplate", "template", "Template"),
        )
        if component is not None:
            add_component(components_by_key, component, component_source or "safe_simple_construction_script")
        else:
            add_component_node_placeholder(components_by_key, node, source or "safe_simple_construction_script")
    if len(components_by_key) > count_before:
        STATE.info("Safe SCS scan collected {} component templates/placeholders.".format(len(components_by_key) - count_before))


def collect_component_templates_safe(objects, components_by_key):
    count_before = len(components_by_key)
    for obj, label in objects:
        if obj is None:
            continue
        for prop_name in ("component_templates", "ComponentTemplates"):
            ok, raw, method = raw_property_value(obj, prop_name)
            if not ok:
                raw = get_prop(obj, prop_name)
                method = "get_prop"
            if not raw:
                continue
            if is_component_like_object(raw):
                add_component(components_by_key, raw, "safe_{}.{} via {}".format(label, prop_name, method))
            for component in iter_unreal_collection(raw):
                if is_component_like_object(component):
                    add_component(components_by_key, component, "safe_{}.{} via {}".format(label, prop_name, method))
    if len(components_by_key) > count_before:
        STATE.info("Safe component template scan collected {} component templates.".format(len(components_by_key) - count_before))


def read_components_suggestions(asset_dir):
    path = os.path.join(asset_dir, "output", "components_suggestions.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        components = data.get("components", []) if isinstance(data, dict) else []
        return components if isinstance(components, list) else []
    except Exception as exc:
        STATE.warn("Could not read components_suggestions.json: {}".format(exc))
        return []


def read_existing_components(asset_dir):
    path = os.path.join(asset_dir, "components.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        components = data.get("components", []) if isinstance(data, dict) else []
        return components if isinstance(components, list) else []
    except Exception as exc:
        STATE.warn("Could not read existing components.json: {}".format(exc))
        return []


def add_component_placeholder(components_by_key, item, source):
    if not isinstance(item, dict):
        return
    name = str(item.get("name") or "").strip()
    if not name:
        return
    existing = {str(existing_item.get("name", "")).lower() for existing_item in components_by_key.values() if isinstance(existing_item, dict)}
    if name.lower() in existing:
        return
    class_hint = str(item.get("class") or component_class_hint(name))
    key = source + ":" + name.lower()
    components_by_key[key] = {
        "name": name,
        "class": class_hint,
        "path": str(item.get("path") or ""),
        "defaults": item.get("defaults", {}) if isinstance(item.get("defaults", {}), dict) else {},
        "purpose": str(item.get("purpose") or ""),
        "source": str(item.get("source") or source),
        "_reads": item.get("_reads", 0),
        "_writes": item.get("_writes", 0),
        "_todo": str(item.get("_todo") or "Candidate from Blueprint graph usage. Confirm the real component/template in ARK DevKit."),
    }


def add_component_candidate_placeholders(components_by_key, asset_dir):
    suggestions = read_components_suggestions(asset_dir)
    for item in suggestions:
        add_component_placeholder(components_by_key, item, "analysis_candidate")
    if not suggestions:
        existing = read_existing_components(asset_dir)
        for item in existing:
            add_component_placeholder(components_by_key, item, "existing_components")
        if existing:
            STATE.info("Preserved {} existing components because components_suggestions.json had no new candidates.".format(len(existing)))


def collect_components(blueprint, cdo, generated_class=None, asset_dir=None):
    if not INCLUDE_COMPONENT_DEFAULTS:
        return []
    components_by_key = {}
    if SAFE_COMPONENT_EXPORT or not ENABLE_UNSAFE_COMPONENT_REFLECTION:
        STATE.info("Safe component export enabled: using shallow SCS/component-template scan and skipping recursive live component reflection.")
        objects = (
            (blueprint, "blueprint_asset"),
            (generated_class, "generated_class"),
        )
        collect_scs_components_safe(objects, components_by_key)
        collect_component_templates_safe(objects, components_by_key)
    else:
        objects = (
            (blueprint, "blueprint_asset"),
            (generated_class, "generated_class"),
            (cdo, "class_default_object"),
        )
        collect_scs_components(objects, components_by_key)
        collect_blueprint_component_templates(objects, components_by_key)
        collect_cdo_components(cdo, components_by_key)
        for obj, label in objects:
            collect_component_like_references(obj, components_by_key, label)
    if asset_dir:
        add_component_candidate_placeholders(components_by_key, asset_dir)
    return sorted(components_by_key.values(), key=lambda item: str(item.get("name", "")).lower())


def read_defaults_suggestions(asset_dir):
    path = os.path.join(asset_dir, "output", "defaults_suggestions.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        variables = data.get("variables", {}) if isinstance(data, dict) else {}
        return variables if isinstance(variables, dict) else {}
    except Exception as exc:
        STATE.warn("Could not read defaults_suggestions.json: {}".format(exc))
        return {}


def build_suggestion_match_report(suggestions, exported_variables, exported_class_defaults):
    if not suggestions:
        return {"suggestions_found": False, "matched": [], "missing": [], "missing_details": [], "missing_groups": {}}
    exported_keys = set(exported_variables) | set(exported_class_defaults)
    matched = []
    missing = []
    missing_details = []
    missing_groups = {}
    for name in sorted(suggestions):
        if name in exported_keys:
            matched.append(name)
        else:
            missing.append(name)
            item = suggestions.get(name, {}) if isinstance(suggestions, dict) else {}
            if not isinstance(item, dict):
                item = {}
            reads = int(item.get("_reads", 0) or 0)
            writes = int(item.get("_writes", 0) or 0)
            hint = str(item.get("_hint") or "")
            if writes >= 2:
                triage = "graph_written_runtime_state"
                recommendation = "图里会多处写入，更像运行时状态；通常不用手填 Class Default。"
            elif writes == 1:
                triage = "graph_written_maybe_runtime_state"
                recommendation = "图里会写入一次，先确认它是否真的是本资产默认值。"
            elif reads >= 4:
                triage = "likely_parent_or_inherited_state"
                recommendation = "只读且读取频繁，更像父类/原生状态；优先在父类或原生行为里确认。"
            else:
                triage = "needs_manual_default_check"
                recommendation = "仍可能是缺失默认值；改玩法前建议在 DevKit Class Defaults 里复查。"
            missing_groups[triage] = missing_groups.get(triage, 0) + 1
            missing_details.append(
                {
                    "name": name,
                    "hint": hint,
                    "reads": reads,
                    "writes": writes,
                    "triage": triage,
                    "recommendation": recommendation,
                }
            )
    return {
        "suggestions_found": True,
        "matched": matched,
        "missing": missing,
        "missing_details": missing_details,
        "missing_groups": missing_groups,
    }


def render_report(asset_name, discovery_source, asset_path, generated_class, parent_class, defaults_payload, components_payload, suggestion_report):
    lines = [
        "# DevKit Blueprint Defaults Export Report",
        "",
        "## Summary",
        "",
        "- Asset: {}".format(asset_name),
        "- Discovery source: {}".format(discovery_source or "-"),
        "- Asset path: {}".format(asset_path or "-"),
        "- Generated class: {}".format(object_name(generated_class) or "-"),
        "- Parent class: {}".format(parent_class or "-"),
        "- Blueprint variables exported: {}".format(len(defaults_payload.get("variables", {}))),
        "- Class defaults exported: {}".format(len(defaults_payload.get("classDefaults", {}))),
        "- Components exported: {}".format(len(components_payload.get("components", []))),
        "- Warnings: {}".format(len(STATE.warnings)),
        "- Errors: {}".format(len(STATE.errors)),
        "- Skipped unique properties: {}".format(len(STATE.skipped)),
        "- Skipped read attempts: {}".format(STATE.skipped_attempts),
        "",
        "## Analyzer Suggestions Match",
        "",
    ]
    if suggestion_report.get("suggestions_found"):
        lines.append("- Matched suggested defaults: {}".format(len(suggestion_report.get("matched", []))))
        lines.append("- Still missing suggested defaults: {}".format(len(suggestion_report.get("missing", []))))
        missing = suggestion_report.get("missing", [])[:80]
        if missing:
            lines.append("")
            lines.append("Missing suggested defaults by triage:")
            for name, count in sorted(suggestion_report.get("missing_groups", {}).items()):
                lines.append("- {}: {}".format(name, count))
            lines.append("")
            lines.append("| Name | Hint | Reads | Writes | Triage | Recommendation |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for item in suggestion_report.get("missing_details", [])[:80]:
                lines.append(
                    "| {} | {} | {} | {} | {} | {} |".format(
                        item.get("name", ""),
                        item.get("hint", ""),
                        item.get("reads", 0),
                        item.get("writes", 0),
                        item.get("triage", ""),
                        item.get("recommendation", ""),
                    )
                )
    else:
        lines.append("- No existing output/defaults_suggestions.json was found for this asset.")

    if STATE.warnings:
        lines.extend(["", "## Warnings", ""])
        for item in STATE.warnings:
            lines.append("- {}".format(item))

    if STATE.errors:
        lines.extend(["", "## Errors", ""])
        for item in STATE.errors:
            lines.append("- {}".format(item))

    if STATE.skipped:
        lines.extend(["", "## Skipped Properties", ""])
        lines.append("Repeated failures are grouped so this table stays readable.")
        lines.append("")
        lines.append("| Count | Where | Name | Reason |")
        lines.append("| --- | --- | --- | --- |")
        for item in STATE.skipped[:300]:
            lines.append("| {} | {} | {} | {} |".format(item.get("count", 1), item.get("where", ""), item.get("name", ""), item.get("reason", "")))
        if len(STATE.skipped) > 300:
            lines.append("")
            lines.append("- {} additional skipped properties omitted from this report.".format(len(STATE.skipped) - 300))
    lines.append("")
    return "\n".join(lines)


def render_failure_report(traceback_text):
    lines = [
        "# DevKit Blueprint Defaults Export Failure",
        "",
        "## Traceback",
        "",
        "```text",
        str(traceback_text).strip(),
        "```",
        "",
        "## Request",
        "",
        "```json",
        json.dumps(read_request_payload(), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Debug",
        "",
    ]
    if STATE.debug:
        lines.extend("- {}".format(item) for item in STATE.debug)
    else:
        lines.append("- No debug messages were captured before failure.")

    if STATE.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend("- {}".format(item) for item in STATE.warnings)

    if STATE.errors:
        lines.extend(["", "## Errors", ""])
        lines.extend("- {}".format(item) for item in STATE.errors)

    if STATE.skipped:
        lines.extend(["", "## Skipped Attempts", ""])
        lines.append("| Where | Name | Reason |")
        lines.append("| --- | --- | --- |")
        for item in STATE.skipped[:200]:
            lines.append("| {} | {} | {} |".format(item.get("where", ""), item.get("name", ""), item.get("reason", "")))
    lines.append("")
    return "\n".join(lines)


def export_current_blueprint_defaults():
    if unreal is None:
        raise RuntimeError("This script must run inside ARK DevKit / Unreal Editor Python, where the unreal module is available.")

    blueprint, discovery_source = find_current_blueprint()
    if blueprint is None:
        raise RuntimeError("No Blueprint asset found. Open the Blueprint editor, select the Blueprint in Content Browser, or set ASSET_PATH at the top of this script.")

    requested_path = read_request_asset_path()
    asset_path = object_path(blueprint)
    STATE.info("Discovery source: {}".format(discovery_source or "-"))
    STATE.info("Blueprint object: name={}, class={}, path={}".format(object_name(blueprint), class_name(blueprint), asset_path))
    if requested_path:
        STATE.info("Request asset path: {}".format(requested_path))

    generated_class = blueprint_generated_class(blueprint)
    if generated_class is None:
        generated_class = load_blueprint_class(asset_path or requested_path or ASSET_PATH)
    if generated_class is None:
        raise RuntimeError(
            "The selected asset does not expose a generated_class. "
            "Asset name={}, class={}, path={}".format(object_name(blueprint), class_name(blueprint), asset_path)
        )
    cdo = get_cdo(generated_class)
    if cdo is None:
        fallback_class = load_blueprint_class(asset_path or requested_path or ASSET_PATH)
        if fallback_class is not None and fallback_class is not generated_class:
            generated_class = fallback_class
            cdo = get_cdo(generated_class)
    if cdo is None:
        raise RuntimeError(
            "Could not get the generated class default object. "
            "Generated class name={}, class={}, path={}. "
            "Try copying the Blueprint asset reference, not the generated class or folder path.".format(
                object_name(generated_class),
                class_name(generated_class),
                object_path(generated_class),
            )
        )

    asset_name = blueprint_asset_name(blueprint)
    asset_dir = os.path.join(CAPTURE_ROOT, safe_filename(asset_name))
    ensure_dir(asset_dir)

    parent_class = parent_class_name(blueprint, generated_class)
    now = datetime.datetime.now().isoformat(timespec="seconds")

    log("Exporting defaults for {}".format(asset_name))
    log("Output directory: {}".format(asset_dir))

    variables = collect_blueprint_variable_defaults(blueprint, cdo, generated_class)
    class_defaults = collect_class_defaults(cdo, variables.keys(), generated_class=generated_class)
    components = collect_components(blueprint, cdo, generated_class=generated_class, asset_dir=asset_dir)

    defaults_payload = {
        "schema": "blueprint-translator.defaults.v1",
        "generated": now,
        "asset_name": asset_name,
        "object_path": asset_path,
        "parent_class": parent_class,
        "generated_class": object_name(generated_class),
        "variables": variables,
        "classDefaults": class_defaults,
        "exporter": {
            "source": "ARK DevKit / Unreal Python",
            "discovery_source": discovery_source,
            "include_inherited_class_defaults": INCLUDE_INHERITED_CLASS_DEFAULTS,
            "max_class_default_properties": MAX_CLASS_DEFAULT_PROPERTIES,
        },
    }
    components_payload = {
        "schema": "blueprint-translator.components.v1",
        "generated": now,
        "asset_name": asset_name,
        "object_path": asset_path,
        "components": components,
        "exporter": {
            "source": "ARK DevKit / Unreal Python",
            "include_component_defaults": INCLUDE_COMPONENT_DEFAULTS,
            "safe_component_export": SAFE_COMPONENT_EXPORT,
            "unsafe_component_reflection_enabled": ENABLE_UNSAFE_COMPONENT_REFLECTION,
            "max_component_properties": MAX_COMPONENT_PROPERTIES,
        },
    }

    suggestions = read_defaults_suggestions(asset_dir)
    suggestion_report = build_suggestion_match_report(suggestions, variables, class_defaults)
    report_text = render_report(
        asset_name,
        discovery_source,
        asset_path,
        generated_class,
        parent_class,
        defaults_payload,
        components_payload,
        suggestion_report,
    )
    log_payload = {
        "schema": "blueprint-translator.devkit-export-log.v1",
        "generated": now,
        "asset_name": asset_name,
        "warnings": STATE.warnings,
        "errors": STATE.errors,
        "skipped": STATE.skipped,
        "skipped_attempts": STATE.skipped_attempts,
        "debug": STATE.debug,
        "suggestions": suggestion_report,
    }

    defaults_path = os.path.join(asset_dir, "defaults.json")
    components_path = os.path.join(asset_dir, "components.json")
    report_path = os.path.join(asset_dir, "devkit_export_report.md")
    log_path = os.path.join(asset_dir, "devkit_export_log.json")
    write_json(defaults_path, defaults_payload)
    write_json(components_path, components_payload)
    write_text(report_path, report_text)
    write_json(log_path, log_payload)

    log("Wrote: {}".format(defaults_path))
    log("Wrote: {}".format(components_path))
    log("Wrote: {}".format(report_path))
    log("Wrote: {}".format(log_path))
    log("Next: run local analyzer with:")
    log(r'python scripts\bp_clipboard_to_prompt.py --asset-dir "{}"'.format(asset_dir))
    return {
        "asset_dir": asset_dir,
        "defaults": defaults_path,
        "components": components_path,
        "report": report_path,
        "log": log_path,
    }


try:
    EXPORT_RESULT = export_current_blueprint_defaults()
except Exception as exc:
    STATE.error(exc)
    details = traceback.format_exc()
    log(details)
    if unreal is not None:
        try:
            fallback_dir = os.path.join(CAPTURE_ROOT, "_devkit_export_failed")
            ensure_dir(fallback_dir)
            write_text(os.path.join(fallback_dir, "devkit_export_failure.md"), render_failure_report(details))
        except Exception:
            pass
    raise
