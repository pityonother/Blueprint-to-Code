"""Read-only external scan of official ARK DevKit scripting surfaces."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arkdev_scripting_probe.contracts import (  # noqa: E402
    INSTALLATION_SCHEMA,
    assert_path_free,
    attach_semantic_digest,
    canonical_json,
)


_TARGET_PLUGINS = ("PythonScriptPlugin", "EditorScriptingUtilities")
_PYTHON_RUNTIME_DLL = re.compile(r"(?i)^python\d+\.dll$")


def _regular_files(root: Path, pattern: str) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.rglob(pattern) if path.is_file()),
        key=lambda path: path.as_posix().casefold(),
    )


def _unique_files(paths: Iterable[Path]) -> list[Path]:
    unique: dict[str, Path] = {}
    for path in paths:
        if path.is_file():
            unique.setdefault(str(path.resolve()).casefold(), path)
    return sorted(unique.values(), key=lambda path: path.as_posix().casefold())


def _project_descriptors(root: Path) -> list[Path]:
    """Find project descriptors without walking the very large Content tree."""

    candidates: list[Path] = []
    for pattern in ("*.uproject", "*/*.uproject", "*/*/*.uproject"):
        candidates.extend(root.glob(pattern))
    return _unique_files(candidates)


def _binary_roots(engine: Path, descriptor_paths: Iterable[Path]) -> list[Path]:
    candidates = [engine / "Binaries"]
    candidates.extend(path.parent / "Binaries" for path in descriptor_paths)
    return sorted(
        {path for path in candidates if path.is_dir()},
        key=lambda path: path.as_posix().casefold(),
    )


def _content_python_roots(
    engine: Path,
    plugin_descriptors: Iterable[Path],
    project_descriptors: Iterable[Path],
) -> tuple[list[Path], list[Path]]:
    engine_candidates = [engine / "Content" / "Python"]
    engine_candidates.extend(
        path.parent / "Content" / "Python" for path in plugin_descriptors
    )
    project_candidates = [
        path.parent / "Content" / "Python" for path in project_descriptors
    ]
    return (
        sorted(
            {path for path in engine_candidates if path.is_dir()},
            key=lambda path: path.as_posix().casefold(),
        ),
        sorted(
            {path for path in project_candidates if path.is_dir()},
            key=lambda path: path.as_posix().casefold(),
        ),
    )


def _load_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _descriptor_index(
    root: Path,
) -> tuple[dict[str, list[dict[str, object]]], list[Path]]:
    result: dict[str, list[dict[str, object]]] = {}
    paths = _regular_files(root, "*.uplugin")
    for path in paths:
        payload = _load_json(path)
        if payload is None:
            continue
        result.setdefault(path.stem, []).append(payload)
    return result, paths


def _project_plugin_state(
    descriptors: Iterable[Path],
    plugin_name: str,
) -> str:
    observed: set[bool] = set()
    for path in descriptors:
        payload = _load_json(path)
        if payload is None:
            continue
        plugins = payload.get("Plugins")
        if not isinstance(plugins, list):
            continue
        for item in plugins:
            if not isinstance(item, dict) or item.get("Name") != plugin_name:
                continue
            enabled = item.get("Enabled")
            if isinstance(enabled, bool):
                observed.add(enabled)
    if observed == {True}:
        return "TRUE"
    if observed == {False}:
        return "FALSE"
    return "UNKNOWN"


def _build_version(root: Path) -> str:
    direct = root / "Build" / "Build.version"
    candidates = (
        [direct]
        if direct.is_file()
        else _regular_files(root / "Build", "Build.version")
    )
    for path in candidates:
        payload = _load_json(path)
        if payload is None:
            continue
        values = [payload.get(key) for key in ("MajorVersion", "MinorVersion", "PatchVersion")]
        if not all(isinstance(value, int) for value in values):
            continue
        changelist = payload.get("Changelist")
        suffix = f"-{changelist}" if isinstance(changelist, int) else ""
        return f"{values[0]}.{values[1]}.{values[2]}{suffix}+UE5"
    return "UNKNOWN"


def _next_action(result: dict[str, object]) -> str:
    if not result["devkitFound"]:
        return "STOP_DEVKIT_NOT_FOUND"
    if not result["pythonScriptPluginPresent"]:
        return "STOP_PYTHON_PLUGIN_NOT_PRESENT"
    if not result["pythonEmbeddedRuntimePresent"]:
        return "STOP_PYTHON_RUNTIME_NOT_PRESENT"
    if result["pythonPluginEnabled"] == "FALSE":
        return "ENABLE_PYTHON_PLUGIN"
    if result["pythonPluginEnabled"] == "UNKNOWN":
        return "CHECK_PLUGIN_ENABLEMENT"
    return "RUN_IN_EDITOR_PROBE"


def scan_installation(
    devkit_root: Path,
    *,
    output_path: Path | None = None,
) -> dict[str, object]:
    """Inspect only descriptors/binaries/config and return a path-free result."""

    root = Path(devkit_root)
    engine = root / "Engine"
    descriptors, plugin_descriptors = _descriptor_index(engine / "Plugins")
    project_descriptors = _project_descriptors(root)
    binary_roots = _binary_roots(engine, plugin_descriptors)
    executables = _unique_files(
        path
        for binary_root in binary_roots
        for path in _regular_files(binary_root, "*Editor.exe")
    )
    binaries = _unique_files(
        path
        for binary_root in binary_roots
        for path in _regular_files(binary_root, "*.dll")
    )
    python_executables = _unique_files(
        path
        for binary_root in binary_roots
        for path in _regular_files(binary_root, "python.exe")
    )

    python_plugin_binaries = any(
        "pythonscriptplugin" in path.name.casefold() for path in binaries
    )
    python_runtime_dll = any(
        _PYTHON_RUNTIME_DLL.fullmatch(path.name) is not None for path in binaries
    )
    editor_utility_descriptor = any(
        name.casefold()
        in {
            "blutility",
            "editorutilitywidget",
            "editorutilitywidgets",
        }
        for name in descriptors
    )
    editor_utility_binary = any(
        "blutility" in path.name.casefold()
        or "editorutility" in path.name.casefold()
        for path in binaries
    )
    content_python_roots, project_python_roots = _content_python_roots(
        engine,
        plugin_descriptors,
        project_descriptors,
    )

    result: dict[str, object] = {
        "schema": INSTALLATION_SCHEMA,
        "devkitFound": bool(executables),
        "devkitBuild": _build_version(engine),
        "pythonScriptPluginPresent": "PythonScriptPlugin" in descriptors,
        "editorScriptingUtilitiesPresent": (
            "EditorScriptingUtilities" in descriptors
        ),
        "editorUtilityPresent": (
            editor_utility_descriptor or editor_utility_binary
        ),
        "pythonEmbeddedRuntimePresent": bool(
            python_executables and python_runtime_dll and python_plugin_binaries
        ),
        "projectDescriptorFound": bool(project_descriptors),
        "pythonPluginEnabled": _project_plugin_state(
            project_descriptors,
            "PythonScriptPlugin",
        ),
        "editorScriptingUtilitiesEnabled": _project_plugin_state(
            project_descriptors,
            "EditorScriptingUtilities",
        ),
        "engineContentPythonPresent": bool(content_python_roots),
        "projectContentPythonPresent": bool(project_python_roots),
        "pluginDescriptorsDetected": sorted(
            name for name in _TARGET_PLUGINS if name in descriptors
        ),
    }
    result["nextManualAction"] = _next_action(result)
    attach_semantic_digest(result)
    assert_path_free(result)
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(canonical_json(result) + "\n", encoding="utf-8")
    return result


_SUMMARY_KEYS = (
    ("DEVKIT_FOUND", "devkitFound"),
    ("DEVKIT_BUILD", "devkitBuild"),
    ("PYTHON_SCRIPT_PLUGIN_PRESENT", "pythonScriptPluginPresent"),
    (
        "EDITOR_SCRIPTING_UTILITIES_PRESENT",
        "editorScriptingUtilitiesPresent",
    ),
    ("EDITOR_UTILITY_PRESENT", "editorUtilityPresent"),
    ("PYTHON_EMBEDDED_RUNTIME_PRESENT", "pythonEmbeddedRuntimePresent"),
    ("PROJECT_DESCRIPTOR_FOUND", "projectDescriptorFound"),
    ("PYTHON_PLUGIN_ENABLED", "pythonPluginEnabled"),
    ("NEXT_MANUAL_ACTION", "nextManualAction"),
)


def _public_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_installation_summary(result: dict[str, object]) -> str:
    assert_path_free(result)
    return "\n".join(
        f"{label}={_public_value(result.get(key, 'UNKNOWN'))}"
        for label, key in _SUMMARY_KEYS
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devkit-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args(argv)
    result = scan_installation(
        options.devkit_root,
        output_path=options.output,
    )
    print(render_installation_summary(result))
    return 0 if result["devkitFound"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["render_installation_summary", "scan_installation"]
