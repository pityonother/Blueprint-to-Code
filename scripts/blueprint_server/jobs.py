"""Bounded background jobs with process-tree cancellation."""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Callable

from blueprint_translator.harvest_build_jobs import HarvestBuildJobManager

from .request import ApiProblem
from .security import redact_sensitive_text


JOB_TIMEOUT_SECONDS = 1800
JOB_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "timed_out"}
DEFAULT_STREAM_LIMIT = 65_536


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


class _BoundedText:
    def __init__(self, limit: int) -> None:
        self.limit = int(limit)
        self.parts: deque[str] = deque()
        self.length = 0
        self.truncated = False

    def append(self, text: str) -> None:
        if not text:
            return
        if len(text) >= self.limit:
            self.parts.clear()
            self.parts.append(text[-self.limit :])
            self.length = self.limit
            self.truncated = True
            return
        self.parts.append(text)
        self.length += len(text)
        while self.length > self.limit and self.parts:
            excess = self.length - self.limit
            first = self.parts[0]
            if len(first) <= excess:
                self.parts.popleft()
                self.length -= len(first)
            else:
                self.parts[0] = first[excess:]
                self.length -= excess
            self.truncated = True

    def value(self) -> str:
        return "".join(self.parts)


@dataclass
class _Job:
    id: str
    kind: str
    title: str
    command: list[str]
    on_complete: Callable[[int], dict[str, object]] | None
    stdout: _BoundedText
    stderr: _BoundedText
    status: str = "queued"
    return_code: int | None = None
    duration_seconds: float = 0
    created_at: str = field(default_factory=_now_iso)
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    result: dict[str, object] = field(default_factory=dict)
    cancel_requested: bool = False
    process: subprocess.Popen[str] | None = None
    thread: threading.Thread | None = None
    windows_job_handle: int | None = None
    termination_started: bool = False


class BackgroundJobManager:
    def __init__(
        self,
        project_root: Path,
        *,
        stream_limit: int = DEFAULT_STREAM_LIMIT,
        timeout_seconds: int = JOB_TIMEOUT_SECONDS,
        terminate_timeout_seconds: float = 2.0,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.stream_limit = int(stream_limit)
        self.timeout_seconds = int(timeout_seconds)
        self.terminate_timeout_seconds = float(terminate_timeout_seconds)
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()

    def create(
        self,
        kind: str,
        title: str,
        command: list[str],
        on_complete: object,
    ) -> dict[str, object]:
        self._prune_finished()
        job = _Job(
            id=uuid.uuid4().hex[:12],
            kind=str(kind),
            title=str(title),
            command=[str(part) for part in command],
            on_complete=on_complete if callable(on_complete) else None,
            stdout=_BoundedText(self.stream_limit),
            stderr=_BoundedText(self.stream_limit),
        )
        worker = threading.Thread(
            target=self._run,
            args=(job,),
            name=f"blueprint-job-{job.id}",
            daemon=True,
        )
        job.thread = worker
        with self._lock:
            self._jobs[job.id] = job
        worker.start()
        return self._snapshot(job)

    def get(self, job_id: str) -> dict[str, object]:
        with self._lock:
            job = self._jobs.get(str(job_id))
            if job is None:
                raise ApiProblem(
                    HTTPStatus.NOT_FOUND,
                    {
                        "ok": False,
                        "code": "job_not_found",
                        "error": "The requested job does not exist.",
                    },
                )
            return self._snapshot_locked(job)

    def cancel(self, job_id: str) -> dict[str, object]:
        process: subprocess.Popen[str] | None = None
        with self._lock:
            job = self._jobs.get(str(job_id))
            if job is None:
                raise ApiProblem(
                    HTTPStatus.NOT_FOUND,
                    {
                        "ok": False,
                        "code": "job_not_found",
                        "error": "The requested job does not exist.",
                    },
                )
            if job.status in JOB_TERMINAL_STATUSES:
                return self._snapshot_locked(job)
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = _now_iso()
                return self._snapshot_locked(job)
            if job.process is not None and not job.termination_started:
                job.termination_started = True
                process = job.process
        if process is not None:
            self._stop_process_tree(process, job)
        return self.get(job_id)

    def _run(self, job: _Job) -> None:
        started = time.monotonic()
        with self._lock:
            if job.cancel_requested:
                job.status = "cancelled"
                job.finished_at = _now_iso()
                return
            job.status = "running"
            job.started_at = _now_iso()
        try:
            process_options: dict[str, object]
            if os.name == "nt":
                process_options = {
                    "creationflags": getattr(
                        subprocess,
                        "CREATE_NEW_PROCESS_GROUP",
                        0x00000200,
                    )
                }
            else:
                process_options = {"start_new_session": True}
            process = subprocess.Popen(
                job.command,
                cwd=str(self.project_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
                **process_options,
            )
            try:
                windows_job_handle = (
                    HarvestBuildJobManager._attach_windows_kill_job(process)
                )
            except BaseException:
                HarvestBuildJobManager._force_stop_unmanaged_process_tree(process)
                raise
            with self._lock:
                job.process = process
                job.windows_job_handle = windows_job_handle
                should_stop = job.cancel_requested and not job.termination_started
                if should_stop:
                    job.termination_started = True
            readers: list[threading.Thread] = []
            for stream_name, stream in (
                ("stdout", process.stdout),
                ("stderr", process.stderr),
            ):
                if stream is None:
                    continue
                reader = threading.Thread(
                    target=self._read_stream,
                    args=(job, stream_name, stream),
                    daemon=True,
                )
                readers.append(reader)
                reader.start()
            if should_stop:
                self._stop_process_tree(process, job)
            timed_out = False
            try:
                return_code = process.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._stop_process_tree(process, job)
                return_code = process.wait()
            for reader in readers:
                reader.join(timeout=2)
            self._cleanup_windows_job(job)

            with self._lock:
                job.return_code = return_code
                job.duration_seconds = round(time.monotonic() - started, 2)
                job.finished_at = _now_iso()
                if timed_out:
                    job.status = "timed_out"
                    job.error = (
                        f"Job exceeded the {self.timeout_seconds}-second limit."
                    )
                elif job.cancel_requested:
                    job.status = "cancelled"
                else:
                    job.status = "succeeded" if return_code == 0 else "failed"
            if job.on_complete is not None:
                result = job.on_complete(return_code)
                with self._lock:
                    job.result = result if isinstance(result, dict) else {}
        except BaseException as exc:
            with self._lock:
                job.status = "cancelled" if job.cancel_requested else "failed"
                job.error = str(exc)
                job.duration_seconds = round(time.monotonic() - started, 2)
                job.finished_at = _now_iso()
        finally:
            self._cleanup_windows_job(job)
            with self._lock:
                job.process = None

    def _read_stream(self, job: _Job, stream_name: str, stream: object) -> None:
        try:
            for line in iter(stream.readline, ""):  # type: ignore[attr-defined]
                with self._lock:
                    target = job.stdout if stream_name == "stdout" else job.stderr
                    target.append(line)
        finally:
            try:
                stream.close()  # type: ignore[attr-defined]
            except Exception:
                pass

    def _stop_process_tree(
        self,
        process: subprocess.Popen[str],
        job: _Job,
    ) -> None:
        if process.poll() is not None:
            self._force_group_cleanup(process, job)
            return
        try:
            HarvestBuildJobManager._signal_process_tree(process, force=False)
        except Exception:
            pass
        try:
            process.wait(timeout=self.terminate_timeout_seconds)
        except subprocess.TimeoutExpired:
            windows_job_handle = self._take_windows_job_handle(job)
            try:
                HarvestBuildJobManager._signal_process_tree(
                    process,
                    force=True,
                    windows_job_handle=windows_job_handle,
                )
            finally:
                if windows_job_handle is not None:
                    HarvestBuildJobManager._close_windows_handle(
                        windows_job_handle
                    )
            try:
                process.wait(timeout=self.terminate_timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
        finally:
            self._force_group_cleanup(process, job)

    def _force_group_cleanup(
        self,
        process: subprocess.Popen[str],
        job: _Job,
    ) -> None:
        if os.name == "nt":
            self._cleanup_windows_job(job)
            return
        try:
            HarvestBuildJobManager._signal_process_tree(process, force=True)
        except (ProcessLookupError, PermissionError):
            pass

    def _cleanup_windows_job(self, job: _Job) -> None:
        handle = self._take_windows_job_handle(job)
        if handle is None:
            return
        try:
            HarvestBuildJobManager._terminate_windows_job(handle)
        finally:
            HarvestBuildJobManager._close_windows_handle(handle)

    def _take_windows_job_handle(self, job: _Job) -> int | None:
        with self._lock:
            handle = job.windows_job_handle
            job.windows_job_handle = None
            return handle

    def _prune_finished(self, limit: int = 60) -> None:
        with self._lock:
            finished = sorted(
                (
                    job.finished_at,
                    job_id,
                )
                for job_id, job in self._jobs.items()
                if job.status in JOB_TERMINAL_STATUSES
            )
            for _finished_at, job_id in finished[
                : max(0, len(finished) - limit)
            ]:
                self._jobs.pop(job_id, None)

    def _snapshot(self, job: _Job) -> dict[str, object]:
        with self._lock:
            return self._snapshot_locked(job)

    def _snapshot_locked(self, job: _Job) -> dict[str, object]:
        roots = (self.project_root, Path.home())
        return {
            "id": job.id,
            "kind": job.kind,
            "title": redact_sensitive_text(
                job.title,
                path_roots=roots,
                redact_absolute_paths=True,
            ),
            "status": job.status,
            "stdout": redact_sensitive_text(
                job.stdout.value(),
                path_roots=roots,
                redact_absolute_paths=True,
            ),
            "stderr": redact_sensitive_text(
                job.stderr.value(),
                path_roots=roots,
                redact_absolute_paths=True,
            ),
            "stdoutTruncated": job.stdout.truncated,
            "stderrTruncated": job.stderr.truncated,
            "returnCode": job.return_code,
            "durationSeconds": job.duration_seconds,
            "createdAt": job.created_at,
            "startedAt": job.started_at,
            "finishedAt": job.finished_at,
            "error": redact_sensitive_text(
                job.error,
                path_roots=roots,
                redact_absolute_paths=True,
            ),
            "result": self._sanitize_value(job.result),
        }

    def _sanitize_value(self, value: object) -> object:
        if isinstance(value, dict):
            return {
                str(key): self._sanitize_value(item)
                for key, item in value.items()
                if str(key).casefold()
                not in {"command", "argv", "environment", "env"}
            }
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize_value(item) for item in value]
        if isinstance(value, str):
            candidate = Path(value)
            if "\n" not in value and candidate.is_absolute():
                try:
                    return candidate.resolve().relative_to(
                        self.project_root
                    ).as_posix()
                except ValueError:
                    return "<local-path>"
            return redact_sensitive_text(
                value,
                path_roots=(self.project_root, Path.home()),
                redact_absolute_paths=True,
            )
        return value


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MANAGER = BackgroundJobManager(_PROJECT_ROOT)


def create_background_job(
    kind: str,
    title: str,
    command: list[str],
    on_complete: object,
) -> dict[str, object]:
    return _MANAGER.create(kind, title, command, on_complete)


def get_job(job_id: str) -> dict[str, object]:
    return _MANAGER.get(job_id)


def cancel_job(job_id: str) -> dict[str, object]:
    return _MANAGER.cancel(job_id)


__all__ = [
    "BackgroundJobManager",
    "DEFAULT_STREAM_LIMIT",
    "JOB_TERMINAL_STATUSES",
    "JOB_TIMEOUT_SECONDS",
    "cancel_job",
    "create_background_job",
    "get_job",
]
