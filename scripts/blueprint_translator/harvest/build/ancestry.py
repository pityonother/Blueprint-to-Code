"""Trace candidate assets to the native PrimalDinoCharacter boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from rank_ark_harvest import uasset_object_path


def _path_from_parent_reference(
    parent: str,
    *,
    content_root: Path,
    class_index: dict[str, Path],
) -> Path | None:
    text = str(parent or "").strip().strip("\"'").replace("\\", "/")
    if text.startswith("/Game/"):
        package = text.split(".", 1)[0].removeprefix("/Game/")
        candidate = (content_root / Path(package + ".uasset")).resolve()
        return candidate if candidate.is_file() else None
    indexed = class_index.get(text) or class_index.get(text.casefold())
    return indexed.resolve() if isinstance(indexed, Path) and indexed.is_file() else None


def _native_primal_dino(parent: str) -> bool:
    normalized = str(parent or "").strip().casefold()
    return normalized in {
        "primaldinocharacter",
        "/script/shootergame.primaldinocharacter",
    } or normalized.endswith(".primaldinocharacter")


def trace_primal_dino_ancestry(
    path: Path,
    *,
    content_root: Path,
    load_asset: Callable[[Path], dict[str, Any]],
    class_index: dict[str, Path],
    max_depth: int = 64,
) -> dict[str, Any]:
    """Trace full parent paths until the native PrimalDinoCharacter boundary."""

    current = Path(path).resolve()
    source_paths: list[str] = []
    object_chain = [uasset_object_path(current, content_root)]
    seen: set[Path] = set()
    for _depth in range(max(1, int(max_depth))):
        if current in seen:
            return {
                "status": "ANCESTRY_CYCLE",
                "objectPathChain": object_chain,
                "sourcePaths": source_paths,
            }
        seen.add(current)
        source_paths.append(str(current))
        fact = load_asset(current)
        parent = str(fact.get("parent") or "")
        if not parent:
            return {
                "status": "PARENT_NOT_RECOVERED",
                "objectPathChain": object_chain,
                "sourcePaths": source_paths,
            }
        object_chain.append(parent)
        if _native_primal_dino(parent):
            return {
                "status": "CONFIRMED",
                "objectPathChain": object_chain,
                "sourcePaths": source_paths,
            }
        if parent.startswith("/Script/"):
            return {
                "status": "NOT_PRIMAL_DINO_CHARACTER",
                "objectPathChain": object_chain,
                "sourcePaths": source_paths,
            }
        parent_path = _path_from_parent_reference(
            parent,
            content_root=content_root,
            class_index=class_index,
        )
        if parent_path is None:
            return {
                "status": "PARENT_ASSET_NOT_FOUND",
                "objectPathChain": object_chain,
                "sourcePaths": source_paths,
                "missingParent": parent,
            }
        current = parent_path
    return {
        "status": "ANCESTRY_DEPTH_EXCEEDED",
        "objectPathChain": object_chain,
        "sourcePaths": source_paths,
    }
