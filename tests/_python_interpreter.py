"""Select subprocess Python interpreters without crossing operating systems."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _selection_inputs(
    root: Path,
    *,
    os_name: str | None,
    current_python: Path | None,
) -> tuple[str, Path, Path]:
    platform_name = os.name if os_name is None else os_name
    current = Path(sys.executable) if current_python is None else Path(current_python)
    bundled = Path(root) / "runtime" / "python" / "python.exe"
    return platform_name, current, bundled


def preferred_python(
    root: Path,
    *,
    os_name: str | None = None,
    current_python: Path | None = None,
) -> Path:
    """Prefer the bundled Windows runtime only from a Windows host."""

    platform_name, current, bundled = _selection_inputs(
        root,
        os_name=os_name,
        current_python=current_python,
    )
    if platform_name == "nt" and bundled.is_file():
        return bundled
    return current


def compatible_python_interpreters(
    root: Path,
    *,
    os_name: str | None = None,
    current_python: Path | None = None,
) -> tuple[Path, ...]:
    """Return every Python runtime that is executable on the current host."""

    platform_name, current, bundled = _selection_inputs(
        root,
        os_name=os_name,
        current_python=current_python,
    )
    interpreters = [current]
    if (
        platform_name == "nt"
        and bundled.is_file()
        and bundled.resolve() != current.resolve()
    ):
        interpreters.append(bundled)
    return tuple(interpreters)
