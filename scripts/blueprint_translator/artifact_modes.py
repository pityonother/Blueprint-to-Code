"""Central policy for Blueprint capture artifact output modes."""

from __future__ import annotations

from typing import Final

from .evidence_writer import write_evidence_artifacts_from_payload


ARTIFACT_MODES: Final[frozenset[str]] = frozenset({"legacy", "dual", "indexed"})

# The 52-asset reconciliation and performance gates passed before this cutover.
# Existing legacy files are still preserved unless the user explicitly runs
# ``--prune-legacy``.
DEFAULT_ARTIFACT_MODE: Final[str] = "indexed"


def normalize_artifact_mode(value: object = None) -> str:
    mode = str(value or DEFAULT_ARTIFACT_MODE).strip().casefold()
    if mode not in ARTIFACT_MODES:
        choices = ", ".join(sorted(ARTIFACT_MODES))
        raise ValueError(f"invalid artifact mode {value!r}; expected one of: {choices}")
    return mode


__all__ = [
    "ARTIFACT_MODES",
    "DEFAULT_ARTIFACT_MODE",
    "normalize_artifact_mode",
    "write_evidence_artifacts_from_payload",
]
