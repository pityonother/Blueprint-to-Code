"""State endpoint dependencies and route matching."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True, slots=True)
class StateRoute:
    version: str
    project_root: Path
    capture_root: Path
    devkit_request_path: Path
    list_assets: Callable[[], list[dict[str, object]]]
    knowledge_base_summary: Callable[[], dict[str, object]]
    read_devkit_request: Callable[[], str]
    devkit_python_command: Callable[[], str]
    devkit_output_log_command: Callable[[], str]

    def state(self) -> dict[str, object]:
        return {
            "version": self.version,
            "projectRoot": str(self.project_root),
            "captureRoot": str(self.capture_root),
            "assets": self.list_assets(),
            "knowledgeBase": self.knowledge_base_summary(),
            "devkitRequestPath": str(self.devkit_request_path),
            "devkitAssetPath": self.read_devkit_request(),
            "devkitPythonCommand": self.devkit_python_command(),
            "devkitOutputLogCommand": self.devkit_output_log_command(),
        }


def state_route_payload(
    path: str,
    load_state: Callable[[], dict[str, object]],
) -> dict[str, object] | None:
    if path != "/api/state":
        return None
    return {
        "ok": True,
        **load_state(),
    }


__all__ = [
    "StateRoute",
    "state_route_payload",
]
