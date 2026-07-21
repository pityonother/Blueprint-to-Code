"""Resolve ARK DevKit Content roots consistently across local tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEVKIT_CONTENT_ROOT_FILE = PROJECT_ROOT / "devkit_content_root.txt"
DEFAULT_CONTENT_ROOTS = (
    Path(r"C:\Program Files\Epic Games\ARKDevkit\Projects\ShooterGame\Content"),
    Path(r"D:\Epic Games\ARKDevkit\Projects\ShooterGame\Content"),
    Path(r"E:\Epic Games\ARKDevkit\Projects\ShooterGame\Content"),
    Path(r"G:\ARKDevkit\Projects\ShooterGame\Content"),
)
MAX_EPIC_MANIFEST_BYTES = 1024 * 1024


def _clean_path_text(value: str | os.PathLike[str] | None) -> str:
    return str(value or "").strip().strip("\"'")


def _split_path_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [
        cleaned
        for item in value.split(os.pathsep)
        if (cleaned := _clean_path_text(item))
    ]


def _config_lines(path: Path | None) -> list[str]:
    if path is None or not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return []
    return [
        cleaned
        for line in lines
        if (cleaned := _clean_path_text(line)) and not cleaned.startswith("#")
    ]


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def default_epic_manifest_dir() -> Path:
    program_data = _clean_path_text(os.environ.get("PROGRAMDATA")) or r"C:\ProgramData"
    return (
        Path(program_data).expanduser()
        / "Epic"
        / "EpicGamesLauncher"
        / "Data"
        / "Manifests"
    )


def _is_ark_devkit_manifest(payload: dict[str, object], install_root: Path) -> bool:
    identity = " ".join(
        str(payload.get(key) or "")
        for key in (
            "AppName",
            "DisplayName",
            "LaunchExecutable",
            "MandatoryAppFolderName",
        )
    )
    identity = f"{identity} {install_root.name}".casefold().replace(" ", "")
    return "arkdevkit" in identity


def discover_epic_launcher_content_roots(
    manifest_dir: Path | None = None,
) -> list[Path]:
    """Read Epic Launcher manifests and return existing ARK DevKit Content roots."""

    root = Path(manifest_dir) if manifest_dir is not None else default_epic_manifest_dir()
    if not root.is_dir():
        return []
    try:
        manifests = sorted(root.glob("*.item"), key=lambda path: path.name.casefold())
    except OSError:
        return []

    discovered: list[Path] = []
    for manifest in manifests:
        try:
            size = manifest.stat().st_size
            if size <= 0 or size > MAX_EPIC_MANIFEST_BYTES:
                continue
            payload = json.loads(
                manifest.read_text(encoding="utf-8-sig", errors="strict")
            )
        except (OSError, UnicodeError, ValueError, RecursionError):
            continue
        if not isinstance(payload, dict):
            continue
        install_text = _clean_path_text(payload.get("InstallLocation"))
        if not install_text:
            continue
        try:
            install_root = Path(install_text).expanduser()
        except (OSError, ValueError):
            continue
        if not _is_ark_devkit_manifest(payload, install_root):
            continue
        content_root = install_root / "Projects" / "ShooterGame" / "Content"
        if content_root.is_dir():
            discovered.append(content_root)
    return _dedupe_paths(discovered)


def devkit_content_root_candidates(
    extra_roots: Iterable[str | os.PathLike[str]] | None = None,
    *,
    config_file: Path | None = DEVKIT_CONTENT_ROOT_FILE,
    default_roots: Iterable[str | os.PathLike[str]] = DEFAULT_CONTENT_ROOTS,
) -> list[tuple[str, Path]]:
    """Return roots in priority order together with diagnostic provenance."""

    candidates: list[tuple[str, Path]] = []
    for env_name in ("ARK_DEVKIT_CONTENT_ROOT", "BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT"):
        for value in _split_path_list(os.environ.get(env_name)):
            candidates.append((f"environment {env_name}", Path(value).expanduser()))
    for env_name in ("ARK_DEVKIT_ROOT", "BLUEPRINT_TO_CODE_DEVKIT_ROOT"):
        for value in _split_path_list(os.environ.get(env_name)):
            candidates.append(
                (
                    f"environment {env_name}",
                    Path(value).expanduser() / "Projects" / "ShooterGame" / "Content",
                )
            )
    for value in _config_lines(config_file):
        source = config_file.name if config_file is not None else "content root config"
        candidates.append((source, Path(value).expanduser()))
    for value in extra_roots or ():
        cleaned = _clean_path_text(value)
        if cleaned:
            candidates.append(("caller-provided root", Path(cleaned).expanduser()))
    candidates.extend(
        ("Epic Games Launcher manifest", root)
        for root in discover_epic_launcher_content_roots()
    )
    candidates.extend(
        ("default guess", Path(_clean_path_text(root)).expanduser())
        for root in default_roots
        if _clean_path_text(root)
    )

    unique: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for source, path in candidates:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append((source, path))
    return unique


def devkit_content_roots(
    extra_roots: Iterable[str | os.PathLike[str]] | None = None,
    *,
    config_file: Path | None = DEVKIT_CONTENT_ROOT_FILE,
    default_roots: Iterable[str | os.PathLike[str]] = DEFAULT_CONTENT_ROOTS,
) -> list[Path]:
    return [
        path
        for _source, path in devkit_content_root_candidates(
            extra_roots,
            config_file=config_file,
            default_roots=default_roots,
        )
    ]


def first_existing_devkit_content_root(
    extra_roots: Iterable[str | os.PathLike[str]] | None = None,
    *,
    config_file: Path | None = DEVKIT_CONTENT_ROOT_FILE,
    default_roots: Iterable[str | os.PathLike[str]] = DEFAULT_CONTENT_ROOTS,
) -> Path | None:
    for root in devkit_content_roots(
        extra_roots,
        config_file=config_file,
        default_roots=default_roots,
    ):
        if root.is_dir():
            return root
    return None
