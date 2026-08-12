"""One-shot read-only introspection of official Unreal Editor scripting APIs."""

from __future__ import annotations

import json
import platform
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from arkdev_scripting_probe.contracts import (  # noqa: E402
    PROBE_SCHEMA,
    PROBE_VERSION,
    assert_path_free,
    attach_semantic_digest,
    canonical_json,
)
from arkdev_scripting_probe.in_editor.arkdev_explicit_graph_snapshot import (  # noqa: E402
    collect_explicit_graph_snapshot,
    empty_explicit_target,
)


_CLASS_NAMES = (
    "Object",
    "ObjectIterator",
    "EdGraph",
    "EdGraphNode",
    "K2Node",
    "BlueprintGraphEditor",
    "BlueprintGraphPinLibrary",
    "AssetEditorSubsystem",
    "BlueprintEditorLibrary",
    "EditorUtilitySubsystem",
    "EditorPythonScripting",
    "EditorAssetLibrary",
    "EditorUtilityLibrary",
    "EditorUtilityBlueprint",
    "EditorUtilityWidgetBlueprint",
)
_SAFE_METHODS = {
    "Object": (
        "get_outer",
        "get_typed_outer",
        "get_path_name",
    ),
    "AssetEditorSubsystem": (
        "get_all_edited_assets",
        "find_editor_for_asset",
    ),
    "BlueprintEditorLibrary": (
        "get_blueprint_asset",
        "find_event_graph",
        "find_graph",
        "get_all_graphs",
        "get_all_graph_names",
        "get_node_pos",
        "get_node_size",
        "list_all_pins",
    ),
    "EditorAssetLibrary": ("load_asset",),
    "EditorUtilitySubsystem": (
        "try_run",
        "release_instance_of_asset",
        "register_tab_and_get_id",
    ),
}
_MUTATION_METHODS = {
    "Object": (
        "modify",
        "set_editor_property",
    ),
    "AssetEditorSubsystem": ("open_editor_for_assets",),
    "BlueprintEditorLibrary": (
        "compile_blueprint",
        "add_function_graph",
        "set_node_pos",
        "break_pin_links",
        "create_connection",
    ),
    "EditorAssetLibrary": ("save_asset",),
    "BlueprintGraphEditor": (
        "add_node",
        "create_node",
        "create_connection",
        "remove_node",
    ),
    "BlueprintGraphPinLibrary": (
        "break_pin_links",
        "break_all_pin_links",
        "create_connection",
        "set_default_value",
        "set_default_object",
        "set_default_text",
    ),
}
_VISIBLE_METHOD_TOKENS = (
    "asset",
    "blueprint",
    "compile",
    "connect",
    "connection",
    "create",
    "break",
    "default",
    "disconnect",
    "editor",
    "graph",
    "node",
    "pin",
    "save",
    "utility",
)

_GRAPH_MUTATION_PREFIXES = (
    "add_",
    "break_",
    "connect_",
    "create_",
    "delete_",
    "disconnect_",
    "remove_",
    "set_default",
)


def _member(owner: object, name: str) -> object | None:
    try:
        return getattr(owner, name, None)
    except Exception:
        return None


def _member_status(owner: object | None, name: str) -> str:
    return (
        "AVAILABLE"
        if owner is not None and _member(owner, name) is not None
        else "MISSING"
    )


def _visible_methods(owner: object | None) -> list[str]:
    if owner is None:
        return []
    try:
        names = dir(owner)
    except Exception:
        return []
    return sorted(
        name[:128]
        for name in names
        if not name.startswith("_")
        and any(token in name.casefold() for token in _VISIBLE_METHOD_TOKENS)
    )[:64]


def _graph_mutation_methods(owner: object | None) -> list[str]:
    if owner is None:
        return []
    try:
        names = dir(owner)
    except Exception:
        return []
    return sorted(
        name[:128]
        for name in names
        if not name.startswith("_")
        and any(name.casefold().startswith(prefix) for prefix in _GRAPH_MUTATION_PREFIXES)
    )[:128]


def _engine_version(unreal_module: object | None) -> str:
    if unreal_module is None:
        return ""
    system_library = _member(unreal_module, "SystemLibrary")
    getter = _member(system_library, "get_engine_version")
    if callable(getter):
        try:
            return str(getter())[:128]
        except Exception:
            return ""
    getter = _member(unreal_module, "get_engine_version")
    if callable(getter):
        try:
            return str(getter())[:128]
        except Exception:
            return ""
    return ""


def _introspection(
    unreal_module: object | None,
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, list[str]],
    list[dict[str, str]],
]:
    if unreal_module is None:
        methods = {
            f"{class_name}.{method_name}": "NOT_TESTED"
            for class_name, method_names in _SAFE_METHODS.items()
            for method_name in method_names
        }
        methods.update(
            {
                f"{class_name}.{method_name}": "NOT_TESTED"
                for class_name, method_names in _MUTATION_METHODS.items()
                for method_name in method_names
            }
        )
        return (
            {name: "NOT_TESTED" for name in _CLASS_NAMES},
            methods,
            {name: [] for name in _CLASS_NAMES},
            [],
        )
    classes: dict[str, str] = {}
    methods: dict[str, str] = {}
    visible: dict[str, list[str]] = {}
    mutation_observed: list[dict[str, str]] = []
    for class_name in _CLASS_NAMES:
        owner = _member(unreal_module, class_name)
        classes[class_name] = "AVAILABLE" if owner is not None else "MISSING"
        visible[class_name] = _visible_methods(owner)
        for method_name in _SAFE_METHODS.get(class_name, ()):
            methods[f"{class_name}.{method_name}"] = _member_status(
                owner,
                method_name,
            )
        for method_name in _MUTATION_METHODS.get(class_name, ()):
            if owner is not None and _member(owner, method_name) is not None:
                status = "PRESENT_BUT_NOT_USED"
                mutation_observed.append(
                    {
                        "owner": class_name,
                        "method": method_name,
                        "status": status,
                    }
                )
            else:
                status = "MISSING"
            methods[f"{class_name}.{method_name}"] = status
        if class_name in {"BlueprintGraphEditor", "BlueprintGraphPinLibrary"}:
            for method_name in _graph_mutation_methods(owner):
                key = f"{class_name}.{method_name}"
                methods[key] = "PRESENT_BUT_NOT_USED"
                observation = {
                    "owner": class_name,
                    "method": method_name,
                    "status": "PRESENT_BUT_NOT_USED",
                }
                if observation not in mutation_observed:
                    mutation_observed.append(observation)
    mutation_observed.sort(key=lambda item: (item["owner"], item["method"]))
    return classes, methods, visible, mutation_observed


def _active_editor_probe(
    unreal_module: object | None,
    methods: Mapping[str, str],
) -> tuple[dict[str, str], list[str]]:
    result = {
        "openEditedAssets": "NOT_TESTED",
        "activeAsset": "NOT_TESTED",
        "focusedGraph": "NOT_TESTED",
        "selection": "NOT_TESTED",
        "dirtyState": "NOT_TESTED",
        "compileState": "NOT_TESTED",
    }
    if unreal_module is None:
        return result, ["UNREAL_IMPORT_FAILED"]
    result.update(
        {
            "activeAsset": "MISSING",
            "focusedGraph": "MISSING",
            "selection": "MISSING",
            "dirtyState": "MISSING",
            "compileState": "MISSING",
        }
    )
    if methods.get("AssetEditorSubsystem.get_all_edited_assets") != "AVAILABLE":
        result["openEditedAssets"] = "MISSING"
        return result, ["OPEN_EDITED_ASSETS_API_MISSING", "ACTIVE_EDITOR_IDENTITY_UNAVAILABLE"]
    subsystem_class = _member(unreal_module, "AssetEditorSubsystem")
    getter = _member(unreal_module, "get_editor_subsystem")
    if not callable(getter):
        result["openEditedAssets"] = "ERROR"
        return result, ["EDITOR_SUBSYSTEM_ACCESS_UNAVAILABLE"]
    try:
        subsystem = getter(subsystem_class)
        list_assets = _member(subsystem, "get_all_edited_assets")
        if not callable(list_assets):
            raise RuntimeError
        list(list_assets())
    except Exception:
        result["openEditedAssets"] = "ERROR"
        return result, ["OPEN_EDITED_ASSETS_CALL_FAILED"]
    result["openEditedAssets"] = "AVAILABLE"
    return result, ["ACTIVE_EDITOR_IDENTITY_UNAVAILABLE"]


def _editor_utility_probe(
    classes: Mapping[str, str],
    methods: Mapping[str, str],
) -> dict[str, str]:
    startup_methods = (
        methods.get("EditorUtilitySubsystem.try_run"),
        methods.get("EditorUtilitySubsystem.release_instance_of_asset"),
        methods.get("EditorUtilitySubsystem.register_tab_and_get_id"),
    )
    if any(status == "AVAILABLE" for status in startup_methods):
        startup = "AVAILABLE"
    elif classes.get("EditorUtilitySubsystem") == "AVAILABLE":
        startup = "MISSING"
    else:
        startup = "NOT_TESTED" if not classes else "MISSING"
    return {
        "subsystem": classes.get("EditorUtilitySubsystem", "NOT_TESTED"),
        "blueprintClass": classes.get("EditorUtilityBlueprint", "NOT_TESTED"),
        "widgetClass": classes.get(
            "EditorUtilityWidgetBlueprint",
            "NOT_TESTED",
        ),
        "startupObjectCapability": startup,
    }


def build_probe_bundle(
    unreal_module: object | None,
    *,
    request: Mapping[str, object] | None,
    generated_at: str,
    python_version: str,
) -> tuple[dict[str, object], dict[str, object] | None]:
    classes, methods, visible, mutation_observed = _introspection(unreal_module)
    active_editor, active_gaps = _active_editor_probe(unreal_module, methods)
    gaps = list(active_gaps)
    snapshot: dict[str, object] | None = None
    if unreal_module is None:
        explicit_target = empty_explicit_target(
            status="NOT_TESTED",
            gap="UNREAL_IMPORT_FAILED",
        )
    elif request is None:
        explicit_target = empty_explicit_target(
            status="NOT_TESTED",
            gap="EXPLICIT_TARGET_NOT_REQUESTED",
        )
        gaps.append("EXPLICIT_TARGET_NOT_REQUESTED")
    else:
        try:
            explicit_target, snapshot = collect_explicit_graph_snapshot(
                unreal_module,
                request,
            )
        except Exception:
            explicit_target = empty_explicit_target(
                status="ERROR",
                gap="EXPLICIT_TARGET_PROBE_FAILED",
                request=request,
            )
        gaps.extend(str(item) for item in explicit_target.get("gaps", []))

    engine_version = _engine_version(unreal_module)
    result: dict[str, object] = {
        "schema": PROBE_SCHEMA,
        "probeVersion": PROBE_VERSION,
        "generatedAt": generated_at,
        "devkit": {
            "engineVersion": engine_version,
            "buildVersion": engine_version,
        },
        "python": {
            "available": True,
            "unrealImport": unreal_module is not None,
            "pythonVersion": python_version[:64],
            "pluginDetected": unreal_module is not None,
        },
        "classes": classes,
        "methods": methods,
        "visibleMethods": visible,
        "explicitTarget": explicit_target,
        "activeEditor": active_editor,
        "editorUtility": _editor_utility_probe(classes, methods),
        "mutationApisObserved": mutation_observed,
        "gaps": sorted(set(gaps)),
    }
    attach_semantic_digest(result)
    assert_path_free(result)
    return result, snapshot


def build_probe_result(
    unreal_module: object | None,
    *,
    request: Mapping[str, object] | None,
    generated_at: str,
    python_version: str,
) -> dict[str, object]:
    result, _snapshot = build_probe_bundle(
        unreal_module,
        request=request,
        generated_at=generated_at,
        python_version=python_version,
    )
    return result


def _load_request(path: Path) -> Mapping[str, object] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"schema": "INVALID"}
    return value if isinstance(value, Mapping) else {"schema": "INVALID"}


def _write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def main() -> int:
    try:
        import unreal  # type: ignore[import-not-found]
    except Exception:
        unreal = None
    repo_root = Path(__file__).resolve().parents[3]
    output_root = repo_root / ".arkdev-probe"
    request = _load_request(output_root / "request.json")
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    result, snapshot = build_probe_bundle(
        unreal,
        request=request,
        generated_at=generated_at,
        python_version=platform.python_version(),
    )
    _write(output_root / "live-probe.json", result)
    snapshot_path = output_root / "explicit-graph-snapshot.json"
    target = result["explicitTarget"]
    assert isinstance(target, Mapping)
    if snapshot is not None and target.get("status") == "AVAILABLE":
        _write(snapshot_path, snapshot)
    elif snapshot_path.is_file():
        snapshot_path.unlink()
    print("ARKDEV_OFFICIAL_SCRIPTING_PROBE=COMPLETE")
    python = result["python"]
    assert isinstance(python, Mapping)
    print(f"PYTHON_RUNTIME={'PASS' if python['available'] else 'ERROR'}")
    print(
        f"UNREAL_IMPORT={'PASS' if python['unrealImport'] else 'UNAVAILABLE'}"
    )
    print(f"EXPLICIT_TARGET={target['status']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        print("ARKDEV_OFFICIAL_SCRIPTING_PROBE=ERROR")
        raise SystemExit(1) from None


__all__ = ["build_probe_bundle", "build_probe_result"]
