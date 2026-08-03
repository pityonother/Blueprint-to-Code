"""Discover creature candidates and manage their scan cache."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from ...creature_asset_scan_cache import CreatureAssetScanCache
from .constants import CREATURE_CANDIDATE_PATTERNS, CREATURE_EXTRACTOR_VERSION


def _open_creature_scan_cache(
    args: argparse.Namespace,
) -> CreatureAssetScanCache | None:
    if args.no_scan_cache:
        return None
    return CreatureAssetScanCache(
        args.scan_cache.resolve(),
        refresh=bool(args.refresh_scan_cache),
        extractor_version=CREATURE_EXTRACTOR_VERSION,
    )


def _content_root(devkit_root: Path) -> Path:
    return Path(devkit_root) / "Projects" / "ShooterGame" / "Content"


def discover_creature_candidates(
    content_root: Path,
    *,
    prefer_rg: bool = True,
) -> tuple[list[Path], str]:
    """Discover the broad Character-named family, then prove ancestry per asset.

    This remains a filename candidate set rather than a global Unreal class
    registry.  The wider pattern is intentional: current DevKit assets such as
    ``EndBoss_Character`` and ``Trilobite_Character`` are confirmed
    PrimalDinoCharacter descendants but do not contain ``Character_BP``.
    """

    root = Path(content_root).resolve()
    rg = shutil.which("rg") if prefer_rg else None
    if rg:
        completed = subprocess.run(
            [
                rg,
                "--files",
                *(
                    argument
                    for pattern in CREATURE_CANDIDATE_PATTERNS
                    for argument in ("-g", pattern)
                ),
                str(root),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode in {0, 1}:
            return (
                sorted(
                    {
                        Path(line.strip()).resolve()
                        for line in completed.stdout.splitlines()
                        if line.strip()
                    }
                ),
                "RIPGREP",
            )

    paths: list[Path] = []
    for directory, _subdirectories, filenames in os.walk(root):
        base = Path(directory)
        for filename in filenames:
            folded = filename.casefold()
            if filename.endswith(".uasset") and (
                "character" in folded or "char_bp" in folded
            ):
                paths.append((base / filename).resolve())
    return sorted(set(paths)), "OS_WALK"
