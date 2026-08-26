"""Strict-handle, atomically written local Solver metadata store."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import BinaryIO, Iterator

if os.name == "nt":  # pragma: no cover - selected by platform
    import msvcrt
else:  # pragma: no cover - selected by platform
    import fcntl

from ..contracts import McpExecutionError


_SOLVER_ID = re.compile(r"^solver://(?P<opaque>[0-9a-f]{32})$")
_DOCUMENT_PATHS = {
    "requirement": "requirement.json",
    "researchPlan": "research-plan.json",
    "evidenceMatrix": "evidence-matrix.json",
    "acquisitionPlan": "acquisition-plan.json",
    "state": "state.json",
    "bindings": "bindings.json",
}
_LOCK_TIMEOUT_SECONDS = 10.0
_LOCK_RETRY_SECONDS = 0.01


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class SolverStore:
    """Own the six JSON documents for one local Solver run."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        if self.root.name != ".blueprint-solvers":
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "Solver metadata root must be a dedicated .blueprint-solvers directory.",
            )

    @staticmethod
    def solver_opaque_id(solver_id: str) -> str:
        match = _SOLVER_ID.fullmatch(str(solver_id))
        if match is None:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "solverId must be one opaque solver:// handle.",
            )
        return match.group("opaque")

    def create_solver(
        self,
        solver_id: str,
        documents: Mapping[str, Mapping[str, object]],
    ) -> None:
        normalized = self._documents(documents)
        opaque_id = self.solver_opaque_id(solver_id)
        solver_dir = self.root / opaque_id
        self.root.mkdir(parents=True, exist_ok=True)
        with self._generation_lock():
            self._recover_generation(opaque_id)
            if solver_dir.exists():
                raise McpExecutionError(
                    "INTERNAL_CONTRACT_ERROR",
                    "The generated Solver handle collided with existing metadata.",
                )
            staging_dir = self._pending_dir(opaque_id)
            try:
                staging_dir.mkdir()
                self._write_documents(staging_dir, normalized)
                os.replace(staging_dir, solver_dir)
            except FileExistsError as exc:
                self._discard_generation(staging_dir)
                raise McpExecutionError(
                    "INTERNAL_CONTRACT_ERROR",
                    "The generated Solver handle collided with existing metadata.",
                ) from exc
            except OSError as exc:
                self._discard_generation(staging_dir)
                raise McpExecutionError(
                    "INTERNAL_CONTRACT_ERROR",
                    "Local Solver metadata could not be published atomically.",
                ) from exc

    def load_solver(self, solver_id: str) -> dict[str, dict[str, object]]:
        opaque_id = self.solver_opaque_id(solver_id)
        if not self.root.is_dir():
            raise McpExecutionError(
                "SOLVER_NOT_FOUND", "Solver metadata was not found."
            )
        with self._generation_lock():
            self._recover_generation(opaque_id)
            return self._load_generation(self.root / opaque_id)

    def _load_generation(self, solver_dir: Path) -> dict[str, dict[str, object]]:
        if not solver_dir.is_dir():
            raise McpExecutionError(
                "SOLVER_NOT_FOUND", "Solver metadata was not found."
            )
        try:
            filenames = {path.name for path in solver_dir.iterdir() if path.is_file()}
        except OSError as exc:
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Local Solver metadata could not be read safely.",
            ) from exc
        expected_filenames = set(_DOCUMENT_PATHS.values())
        if not expected_filenames.issubset(filenames):
            raise McpExecutionError(
                "SOLVER_NOT_FOUND", "Solver metadata was not found."
            )
        if filenames != expected_filenames:
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Local Solver metadata must contain exactly six versioned documents.",
            )
        result: dict[str, dict[str, object]] = {}
        for key, filename in _DOCUMENT_PATHS.items():
            result[key] = self._read_json(solver_dir / filename)
        return result

    def save_solver(
        self,
        solver_id: str,
        documents: Mapping[str, Mapping[str, object]],
        *,
        expected_bindings_digest: str | None = None,
    ) -> None:
        normalized = self._documents(documents)
        opaque_id = self.solver_opaque_id(solver_id)
        if not self.root.is_dir():
            raise McpExecutionError(
                "SOLVER_NOT_FOUND", "Solver metadata was not found."
            )
        with self._generation_lock():
            self._recover_generation(opaque_id)
            solver_dir = self.root / opaque_id
            if not solver_dir.is_dir():
                raise McpExecutionError(
                    "SOLVER_NOT_FOUND", "Solver metadata was not found."
                )
            if expected_bindings_digest is not None:
                current = self._load_generation(solver_dir)
                current_digest = str(
                    current["bindings"].get("semanticDigest") or ""
                )
                if current_digest != expected_bindings_digest:
                    raise McpExecutionError(
                        "SOLVER_PHASE_INVALID",
                        "Solver metadata changed before the atomic update was published.",
                    )
            staging_dir = self._pending_dir(opaque_id)
            previous_dir = self._previous_dir(opaque_id)
            try:
                staging_dir.mkdir()
                self._write_documents(staging_dir, normalized)
                os.replace(solver_dir, previous_dir)
                try:
                    os.replace(staging_dir, solver_dir)
                except OSError:
                    os.replace(previous_dir, solver_dir)
                    raise
            except (OSError, TypeError, ValueError) as exc:
                self._discard_generation(staging_dir)
                raise McpExecutionError(
                    "INTERNAL_CONTRACT_ERROR",
                    "Local Solver metadata could not be written atomically.",
                ) from exc
            self._discard_generation(previous_dir)

    def _solver_dir(self, solver_id: str) -> Path:
        return self.root / self.solver_opaque_id(solver_id)

    def _pending_dir(self, opaque_id: str) -> Path:
        return self.root / f".{opaque_id}.pending"

    def _previous_dir(self, opaque_id: str) -> Path:
        return self.root / f".{opaque_id}.previous"

    @contextlib.contextmanager
    def _generation_lock(self) -> Iterator[None]:
        lock_path = self.root / ".solver-store.lock"
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        try:
            with lock_path.open("a+b") as handle:
                self._ensure_lock_byte(handle)
                while True:
                    try:
                        self._try_lock(handle)
                        break
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            raise McpExecutionError(
                                "INTERNAL_CONTRACT_ERROR",
                                "Timed out acquiring the local Solver metadata lock.",
                            ) from exc
                        time.sleep(_LOCK_RETRY_SECONDS)
                try:
                    yield
                finally:
                    try:
                        self._unlock(handle)
                    except OSError:
                        pass
        except McpExecutionError:
            raise
        except OSError as exc:
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Local Solver metadata lock could not be opened safely.",
            ) from exc

    @staticmethod
    def _ensure_lock_byte(handle: BinaryIO) -> None:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)

    @staticmethod
    def _try_lock(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _recover_generation(self, opaque_id: str) -> None:
        solver_dir = self.root / opaque_id
        staging_dir = self._pending_dir(opaque_id)
        previous_dir = self._previous_dir(opaque_id)
        if solver_dir.is_dir():
            self._discard_generation(staging_dir)
            self._discard_generation(previous_dir)
            return
        if previous_dir.is_dir():
            self._discard_generation(staging_dir)
            try:
                os.replace(previous_dir, solver_dir)
            except OSError as exc:
                raise McpExecutionError(
                    "INTERNAL_CONTRACT_ERROR",
                    "Interrupted Solver metadata publication could not be recovered.",
                ) from exc
            return
        self._discard_generation(staging_dir)

    @staticmethod
    def _discard_generation(path: Path) -> None:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        except OSError:
            pass

    @staticmethod
    def _documents(
        documents: Mapping[str, Mapping[str, object]],
    ) -> dict[str, dict[str, object]]:
        if set(documents) != set(_DOCUMENT_PATHS):
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Solver metadata must contain exactly six versioned documents.",
            )
        result: dict[str, dict[str, object]] = {}
        for key in _DOCUMENT_PATHS:
            value = documents.get(key)
            if not isinstance(value, Mapping):
                raise McpExecutionError(
                    "INTERNAL_CONTRACT_ERROR",
                    "Solver metadata has an invalid JSON shape.",
                )
            result[key] = dict(value)
        return result

    @classmethod
    def _write_documents(
        cls,
        solver_dir: Path,
        documents: Mapping[str, Mapping[str, object]],
    ) -> None:
        for key, filename in _DOCUMENT_PATHS.items():
            cls._atomic_json(solver_dir / filename, dict(documents[key]))

    @staticmethod
    def _write_temp(path: Path, payload: Mapping[str, object]) -> Path:
        raw = (_canonical_json(payload) + "\n").encode("utf-8")
        return SolverStore._write_temp_bytes(path, raw)

    @staticmethod
    def _write_temp_bytes(path: Path, raw: bytes) -> Path:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            return temporary
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise McpExecutionError(
                "SOLVER_NOT_FOUND", "Solver metadata was not found."
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Local Solver metadata could not be read safely.",
            ) from exc
        if not isinstance(payload, dict):
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Local Solver metadata has an invalid JSON shape.",
            )
        return payload

    @staticmethod
    def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
        temporary: Path | None = None
        try:
            temporary = SolverStore._write_temp(path, payload)
            os.replace(temporary, path)
        except (OSError, TypeError, ValueError) as exc:
            try:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Local Solver metadata could not be written atomically.",
            ) from exc


__all__ = ["SolverStore"]
