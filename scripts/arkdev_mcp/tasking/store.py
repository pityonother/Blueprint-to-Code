"""Strict-handle, atomically written local Task metadata store."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

from ..contracts import McpExecutionError
from .canonical import canonical_json


_TASK_ID = re.compile(r"^task://(?P<opaque>[0-9a-f]{32})$")
_PLAN_ID = re.compile(r"^patch-plan://(?P<opaque>[0-9a-f]{32})$")
_SIGNATURE = re.compile(r"^[0-9a-f]{64}$")


class TaskStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @staticmethod
    def task_opaque_id(task_id: str) -> str:
        match = _TASK_ID.fullmatch(str(task_id))
        if match is None:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "taskId must be one opaque task:// handle.",
            )
        return match.group("opaque")

    @staticmethod
    def plan_opaque_id(plan_id: str) -> str:
        match = _PLAN_ID.fullmatch(str(plan_id))
        if match is None:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "planId must be one opaque patch-plan:// handle.",
            )
        return match.group("opaque")

    def create_task(
        self,
        task_id: str,
        context: dict[str, object],
        session: dict[str, object],
    ) -> None:
        task_dir = self._task_dir(task_id)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            task_dir.mkdir()
        except FileExistsError as exc:
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "The generated task handle collided with existing metadata.",
            ) from exc
        (task_dir / "slices").mkdir()
        (task_dir / "plans").mkdir()
        self._atomic_json(task_dir / "context.json", context)
        self._atomic_json(task_dir / "session.json", session)

    def load_context(self, task_id: str) -> dict[str, object]:
        return self._read_json(
            self._task_dir(task_id) / "context.json",
            code="TASK_NOT_FOUND",
            message="Task metadata was not found.",
        )

    def load_session(self, task_id: str) -> dict[str, object]:
        return self._read_json(
            self._task_dir(task_id) / "session.json",
            code="TASK_NOT_FOUND",
            message="Task metadata was not found.",
        )

    def save_task(
        self,
        task_id: str,
        context: dict[str, object],
        session: dict[str, object],
    ) -> None:
        task_dir = self._task_dir(task_id)
        if not task_dir.is_dir():
            raise McpExecutionError("TASK_NOT_FOUND", "Task metadata was not found.")
        self._atomic_json(task_dir / "context.json", context)
        self._atomic_json(task_dir / "session.json", session)

    def save_slice(self, task_id: str, payload: dict[str, object]) -> None:
        signature = str(payload.get("querySignature") or "")
        if _SIGNATURE.fullmatch(signature) is None:
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Graph Slice query signature is invalid.",
            )
        path = self._task_dir(task_id) / "slices" / f"{signature}.json"
        self._atomic_json(path, payload)

    def load_slice(self, task_id: str, query_signature: str) -> dict[str, object]:
        if _SIGNATURE.fullmatch(str(query_signature)) is None:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "Graph Slice query signature is invalid.",
            )
        return self._read_json(
            self._task_dir(task_id) / "slices" / f"{query_signature}.json",
            code="TASK_NOT_FOUND",
            message="Stored Graph Slice was not found.",
        )

    def iter_slices(self, task_id: str) -> Iterator[dict[str, object]]:
        slices_dir = self._task_dir(task_id) / "slices"
        if not slices_dir.is_dir():
            raise McpExecutionError("TASK_NOT_FOUND", "Task metadata was not found.")
        for path in sorted(slices_dir.glob("*.json")):
            if _SIGNATURE.fullmatch(path.stem):
                yield self._read_json(
                    path,
                    code="TASK_NOT_FOUND",
                    message="Stored Graph Slice was not found.",
                )

    def save_plan(
        self,
        task_id: str,
        plan_id: str,
        payload: dict[str, object],
    ) -> None:
        opaque = self.plan_opaque_id(plan_id)
        path = self._task_dir(task_id) / "plans" / f"{opaque}.json"
        self._atomic_json(path, payload)

    def load_plan(self, task_id: str, plan_id: str) -> dict[str, object]:
        opaque = self.plan_opaque_id(plan_id)
        return self._read_json(
            self._task_dir(task_id) / "plans" / f"{opaque}.json",
            code="PATCH_PLAN_NOT_FOUND",
            message="Blueprint Patch Plan metadata was not found.",
        )

    def _task_dir(self, task_id: str) -> Path:
        return self.root / self.task_opaque_id(task_id)

    @staticmethod
    def _read_json(path: Path, *, code: str, message: str) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise McpExecutionError(code, message) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Local task metadata could not be read safely.",
            ) from exc
        if not isinstance(payload, dict):
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Local task metadata has an invalid JSON shape.",
            )
        return payload

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(canonical_json(payload))
                handle.write("\n")
                handle.flush()
            os.replace(temporary, path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Local task metadata could not be written atomically.",
            ) from exc


__all__ = ["TaskStore"]
