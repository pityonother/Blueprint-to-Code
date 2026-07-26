"""Thread-safe, single-job runner for the ARK harvest Explorer build.

The module deliberately does not create a manager or start a process at import
time.  An HTTP layer can own one :class:`HarvestBuildJobManager` and call
``start()``, ``get()``, ``cancel()``, and ``wait()`` without accepting raw
command text from a request.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from blueprint_server.security import redact_sensitive_text


QUEUED = "QUEUED"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"

ACTIVE_STATUSES = frozenset({QUEUED, RUNNING})
TERMINAL_STATUSES = frozenset({SUCCEEDED, FAILED, CANCELLED})

_PROGRESS_LINE = re.compile(r"^\s*\[(\d+)\s*/\s*(\d+)\]\s*(.*)$")
_PATH_ARGUMENTS: tuple[tuple[str, str], ...] = (
    ("devkit_root", "--devkit-root"),
    ("output_dir", "--output-dir"),
    ("catalog_output", "--catalog-output"),
    ("scan_cache", "--scan-cache"),
    ("creature_scan_cache", "--creature-scan-cache"),
    ("map_scan_cache", "--map-scan-cache"),
    ("image_cache_root", "--image-cache-root"),
    ("creature_file", "--creature-file"),
)
_BOOLEAN_ARGUMENTS: tuple[tuple[str, str], ...] = (
    ("skip_map_scan", "--skip-map-scan"),
    ("skip_images", "--skip-images"),
)
_OUTPUT_PATH_TARGETS: dict[str, tuple[str, ...]] = {
    "output_dir": ("analysis", "harvest_rankings"),
    "catalog_output": (
        "analysis",
        "harvest_nodes",
        "resource_node_catalog.json",
    ),
    "scan_cache": (
        "analysis",
        "harvest_nodes",
        "resource_node_scan_cache.json",
    ),
    "creature_scan_cache": (
        "analysis",
        "harvest_rankings",
        "creature_asset_scan_cache.json",
    ),
    "map_scan_cache": (
        "analysis",
        "harvest_nodes",
        "map_reference_scan_cache.json",
    ),
    "image_cache_root": ("analysis", "harvest_nodes", "images"),
}
_ALLOWED_OPTIONS = frozenset(
    {name for name, _flag in _PATH_ARGUMENTS}
    | {name for name, _flag in _BOOLEAN_ARGUMENTS}
)

_PROMOTION_BEGIN_LINE = "[promotion-critical] begin"
_PROMOTION_COMMIT_COMPLETE_LINE = "[promotion-critical] commit-complete"
_PROMOTION_END_LINE = "[promotion-critical] end"
_REAL_POPEN_TYPE = subprocess.Popen


class HarvestBuildJobError(RuntimeError):
    """Base error suitable for translation into an HTTP error response."""

    code = "harvest_build_job_error"


class HarvestBuildArgumentError(HarvestBuildJobError, ValueError):
    code = "invalid_harvest_build_arguments"


class HarvestBuildAlreadyRunning(HarvestBuildJobError):
    code = "harvest_build_already_running"

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"Harvest build {job_id} is already active.")


class HarvestBuildJobNotFound(HarvestBuildJobError, LookupError):
    code = "harvest_build_job_not_found"

    def __init__(self, job_id: str | None = None) -> None:
        self.job_id = job_id
        detail = f" {job_id}" if job_id else ""
        super().__init__(f"Harvest build job{detail} was not found.")


@dataclass
class _Job:
    id: str
    command: list[str]
    status: str = QUEUED
    pid: int | None = None
    return_code: int | None = None
    created_at: str = field(default_factory=lambda: _utc_now())
    started_at: str | None = None
    finished_at: str | None = None
    error: str = ""
    cancel_requested: bool = False
    log_tail: str = ""
    log_truncated: bool = False
    progress: dict[str, object] = field(
        default_factory=lambda: {
            "current": 0,
            "total": 0,
            "label": "",
            "line": "",
        }
    )
    progress_lines: list[str] = field(default_factory=list)
    process: subprocess.Popen[str] | None = None
    worker: threading.Thread | None = None
    termination_started: bool = False
    promotion_critical: bool = False
    promotion_committed: bool = False
    cancellation_deferred: bool = False
    cancel_too_late: bool = False
    windows_job_handle: int | None = None


def _utc_now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")


class HarvestBuildJobManager:
    """Own at most one active ``build_ark_harvest_explorer.py`` process.

    Request data is a mapping of named, typed options.  Raw argv, executable,
    script, cwd, and shell values are never accepted by :meth:`start`.
    Generated output paths are confined to exact option-specific ``analysis``
    target roles, so one output option cannot overwrite another artifact.
    """

    def __init__(
        self,
        *,
        project_root: str | os.PathLike[str] | Path | None = None,
        python_executable: str | os.PathLike[str] | Path | None = None,
        max_log_chars: int = 32_768,
        max_progress_lines: int = 32,
        terminate_timeout_seconds: float = 2.0,
    ) -> None:
        inferred_root = Path(__file__).resolve().parents[2]
        self.project_root = Path(project_root or inferred_root).expanduser().resolve()
        self.script_path = (
            self.project_root / "scripts" / "build_ark_harvest_explorer.py"
        ).resolve()
        executable = os.fspath(python_executable or sys.executable)
        if not executable or "\x00" in executable:
            raise HarvestBuildArgumentError("Invalid Python executable path.")
        self.python_executable = str(Path(executable).expanduser().resolve())
        if isinstance(max_log_chars, bool) or max_log_chars <= 0:
            raise ValueError("max_log_chars must be a positive integer.")
        if isinstance(max_progress_lines, bool) or max_progress_lines <= 0:
            raise ValueError("max_progress_lines must be a positive integer.")
        if terminate_timeout_seconds <= 0:
            raise ValueError("terminate_timeout_seconds must be positive.")
        self.max_log_chars = int(max_log_chars)
        self.max_progress_lines = int(max_progress_lines)
        self.terminate_timeout_seconds = float(terminate_timeout_seconds)
        self._redaction_roots = (self.project_root, Path.home().resolve())
        self._condition = threading.Condition(threading.RLock())
        self._job: _Job | None = None

    @property
    def allowed_options(self) -> frozenset[str]:
        """Return the immutable request-option whitelist."""

        return _ALLOWED_OPTIONS

    def start(self, options: Mapping[str, object] | None = None) -> dict[str, object]:
        """Queue a build and return its initial ``QUEUED`` snapshot.

        The worker is started only by this explicit call.  A second call is
        rejected while the current job is ``QUEUED`` or ``RUNNING``.
        """

        command = self._build_command(options)
        with self._condition:
            if self._job is not None and self._job.status in ACTIVE_STATUSES:
                raise HarvestBuildAlreadyRunning(self._job.id)
            job = _Job(id=uuid.uuid4().hex, command=command)
            worker = threading.Thread(
                target=self._run_job,
                args=(job,),
                name=f"harvest-build-{job.id[:8]}",
                daemon=True,
            )
            job.worker = worker
            self._job = job
            accepted = self._snapshot_locked(job)
        worker.start()
        return accepted

    def get(self, job_id: str | None = None) -> dict[str, object]:
        """Return the current job as a JSON-safe snapshot."""

        with self._condition:
            job = self._require_job_locked(job_id)
            return self._snapshot_locked(job)

    def wait(
        self,
        job_id: str | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, object]:
        """Wait for a terminal state; useful to tests and non-HTTP callers."""

        if timeout is not None and timeout < 0:
            raise ValueError("timeout cannot be negative.")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            job = self._require_job_locked(job_id)
            while job.status not in TERMINAL_STATUSES:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(f"Timed out waiting for harvest build {job.id}.")
                self._condition.wait(remaining)
                job = self._require_job_locked(job.id)
            return self._snapshot_locked(job)

    def cancel(self, job_id: str | None = None) -> dict[str, object]:
        """Cancel the whole build process tree, escalating if needed.

        Repeating cancellation on a terminal job is a no-op.  The method only
        reports ``CANCELLED`` after the tree has exited (or before it starts).
        Cancellation received during promotion is deferred; a successfully
        committed bundle is reported as ``SUCCEEDED`` with ``cancelTooLate``.
        """

        process: subprocess.Popen[str] | None = None
        cancellation_deferred = False
        with self._condition:
            job = self._require_job_locked(job_id)
            if job.status in TERMINAL_STATUSES:
                return self._snapshot_locked(job)
            job.cancel_requested = True
            if job.status == QUEUED:
                self._finish_locked(job, CANCELLED)
                return self._snapshot_locked(job)
            if job.promotion_critical or job.promotion_committed:
                job.cancellation_deferred = True
                job.cancel_too_late = job.promotion_committed
                cancellation_deferred = True
            elif job.process is not None and not job.termination_started:
                job.termination_started = True
                process = job.process
            self._condition.notify_all()

            if cancellation_deferred:
                return self._snapshot_locked(job)

        if process is not None:
            _stopped, error = self._stop_process(process, job)
            with self._condition:
                job = self._require_job_locked(job_id)
                if error and not job.error:
                    job.error = error
                self._condition.notify_all()

        deadline = time.monotonic() + (self.terminate_timeout_seconds * 2) + 0.5
        with self._condition:
            job = self._require_job_locked(job_id)
            while job.status in ACTIVE_STATUSES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
                job = self._require_job_locked(job.id)
            return self._snapshot_locked(job)

    def _build_command(
        self,
        options: Mapping[str, object] | None,
    ) -> list[str]:
        if options is None:
            supplied: Mapping[str, object] = {}
        elif isinstance(options, Mapping):
            supplied = options
        else:
            raise HarvestBuildArgumentError("Build options must be an object mapping.")

        unknown = sorted(str(key) for key in supplied if key not in _ALLOWED_OPTIONS)
        if unknown:
            raise HarvestBuildArgumentError(
                "Unsupported build option(s): " + ", ".join(unknown)
            )

        normalized_paths: dict[str, Path] = {}
        for name, _flag in _PATH_ARGUMENTS:
            if name not in supplied or supplied[name] is None:
                continue
            value = supplied[name]
            if isinstance(value, bool) or not isinstance(value, (str, os.PathLike)):
                raise HarvestBuildArgumentError(f"{name} must be a filesystem path.")
            raw = os.fspath(value)
            if not raw or "\x00" in raw:
                raise HarvestBuildArgumentError(f"{name} is not a valid path.")
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = self.project_root / path
            resolved = path.resolve()
            target_parts = _OUTPUT_PATH_TARGETS.get(name)
            if target_parts is not None:
                allowed_target = self.project_root.joinpath(*target_parts).resolve()
                if resolved != allowed_target:
                    target = "/".join(target_parts)
                    raise HarvestBuildArgumentError(
                        f"{name} must stay within its allowed {target} target role."
                    )
            normalized_paths[name] = resolved

        normalized_booleans: dict[str, bool] = {}
        for name, _flag in _BOOLEAN_ARGUMENTS:
            if name not in supplied:
                continue
            value = supplied[name]
            if type(value) is not bool:
                raise HarvestBuildArgumentError(f"{name} must be true or false.")
            normalized_booleans[name] = value

        command = [self.python_executable, str(self.script_path)]
        for name, flag in _PATH_ARGUMENTS:
            if name in normalized_paths:
                command.extend([flag, str(normalized_paths[name])])
        for name, flag in _BOOLEAN_ARGUMENTS:
            if normalized_booleans.get(name, False):
                command.append(flag)
        return command

    def _run_job(self, job: _Job) -> None:
        with self._condition:
            if self._job is not job or job.status != QUEUED:
                return
            if job.cancel_requested:
                self._finish_locked(job, CANCELLED)
                return
            job.status = RUNNING
            job.started_at = _utc_now()
            self._condition.notify_all()

        try:
            process_group_options: dict[str, object]
            if os.name == "nt":
                process_group_options = {
                    "creationflags": getattr(
                        subprocess,
                        "CREATE_NEW_PROCESS_GROUP",
                        0x00000200,
                    )
                }
            else:
                process_group_options = {"start_new_session": True}
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
                **process_group_options,
            )
            try:
                windows_job_handle = self._attach_windows_kill_job(process)
            except BaseException:
                self._force_stop_unmanaged_process_tree(process)
                raise
            should_stop = False
            with self._condition:
                job.process = process
                job.pid = process.pid
                job.windows_job_handle = windows_job_handle
                if job.cancel_requested and not job.termination_started:
                    job.termination_started = True
                    should_stop = True
                self._condition.notify_all()

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
                    name=f"harvest-build-{stream_name}-{job.id[:8]}",
                    daemon=True,
                )
                readers.append(reader)
                reader.start()

            if should_stop:
                _stopped, error = self._stop_process(process, job)
                with self._condition:
                    if error and not job.error:
                        job.error = error

            return_code = process.wait()
            readers_finished = self._join_reader_threads(readers)
            cleanup_error = ""
            if not readers_finished:
                cleanup_error = self._cleanup_process_tree_after_root_exit(
                    process,
                    job,
                )
                readers_finished = self._join_reader_threads(readers)

            with self._condition:
                job.return_code = return_code
                if cleanup_error and not job.error:
                    job.error = cleanup_error
                if job.status not in TERMINAL_STATUSES:
                    if job.promotion_committed and return_code == 0:
                        if job.cancel_requested:
                            job.cancellation_deferred = True
                            job.cancel_too_late = True
                        self._finish_locked(job, SUCCEEDED, return_code=return_code)
                    elif job.cancel_requested:
                        self._finish_locked(job, CANCELLED, return_code=return_code)
                    elif not readers_finished:
                        self._finish_locked(
                            job,
                            FAILED,
                            return_code=return_code,
                            error=(
                                "Harvest build output streams did not close after "
                                "the process tree was cleaned up."
                            ),
                        )
                    elif cleanup_error:
                        self._finish_locked(
                            job,
                            FAILED,
                            return_code=return_code,
                            error=cleanup_error,
                        )
                    elif return_code == 0:
                        self._finish_locked(job, SUCCEEDED, return_code=return_code)
                    else:
                        self._finish_locked(
                            job,
                            FAILED,
                            return_code=return_code,
                            error=f"Harvest build process exited with code {return_code}.",
                        )
        except BaseException as exc:
            with self._condition:
                if job.status not in TERMINAL_STATUSES:
                    if job.cancel_requested:
                        self._finish_locked(job, CANCELLED, error=str(exc))
                    else:
                        self._finish_locked(job, FAILED, error=str(exc))
        finally:
            with self._condition:
                job.process = None
                self._condition.notify_all()
            windows_job_handle = self._take_windows_job_handle(job)
            if windows_job_handle is not None:
                self._close_windows_handle(windows_job_handle)

    def _read_stream(self, job: _Job, stream_name: str, stream: TextIO) -> None:
        try:
            for raw_line in iter(stream.readline, ""):
                self._append_line(job, stream_name, raw_line)
        except Exception as exc:
            self._append_line(job, stream_name, f"[stream read error] {exc}\n")
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _append_line(self, job: _Job, stream_name: str, raw_line: str) -> None:
        line = raw_line.rstrip("\r\n")
        safe_line = redact_sensitive_text(
            line,
            path_roots=self._redaction_roots,
            redact_absolute_paths=True,
        )
        entry = f"[{stream_name}] {safe_line}\n"
        with self._condition:
            if self._job is not job:
                return
            if line == _PROMOTION_BEGIN_LINE:
                job.promotion_critical = True
                if job.cancel_requested:
                    job.cancellation_deferred = True
            elif line == _PROMOTION_COMMIT_COMPLETE_LINE:
                job.promotion_critical = False
                job.promotion_committed = True
                if job.cancel_requested:
                    job.cancellation_deferred = True
                    job.cancel_too_late = True
            elif line == _PROMOTION_END_LINE:
                job.promotion_critical = False
            combined = job.log_tail + entry
            if len(combined) > self.max_log_chars:
                combined = combined[-self.max_log_chars :]
                job.log_truncated = True
            job.log_tail = combined

            match = _PROGRESS_LINE.fullmatch(line)
            if match:
                current = int(match.group(1))
                total = int(match.group(2))
                label = redact_sensitive_text(
                    match.group(3).strip(),
                    path_roots=self._redaction_roots,
                    redact_absolute_paths=True,
                )
                job.progress = {
                    "current": current,
                    "total": total,
                    "label": label,
                    "line": safe_line,
                }
                job.progress_lines.append(safe_line)
                if len(job.progress_lines) > self.max_progress_lines:
                    del job.progress_lines[
                        : len(job.progress_lines) - self.max_progress_lines
                    ]
            self._condition.notify_all()

    def _join_reader_threads(
        self,
        readers: list[threading.Thread],
    ) -> bool:
        deadline = time.monotonic() + self.terminate_timeout_seconds
        for reader in readers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            reader.join(timeout=remaining)
        return all(not reader.is_alive() for reader in readers)

    def _stop_process(
        self,
        process: subprocess.Popen[str],
        job: _Job,
    ) -> tuple[bool, str]:
        if process.poll() is not None:
            error = self._cleanup_process_tree_after_root_exit(process, job)
            return not error, error
        try:
            self._signal_process_tree(process, force=False)
        except Exception as exc:
            if process.poll() is not None:
                error = self._cleanup_process_tree_after_root_exit(process, job)
                return not error, error
            terminate_error = f"Could not terminate harvest build process tree: {exc}"
        else:
            terminate_error = ""
        try:
            process.wait(timeout=self.terminate_timeout_seconds)
            self._cleanup_windows_job_after_root_exit(job)
            return True, terminate_error
        except subprocess.TimeoutExpired:
            pass

        while process.poll() is None:
            with self._condition:
                defer_force = job.promotion_critical or job.promotion_committed
            if not defer_force:
                break
            try:
                process.wait(timeout=0.05)
                self._cleanup_windows_job_after_root_exit(job)
                return True, terminate_error
            except subprocess.TimeoutExpired:
                continue

        windows_job_handle = self._take_windows_job_handle(job)
        try:
            self._signal_process_tree(
                process,
                force=True,
                windows_job_handle=windows_job_handle,
            )
        except Exception as exc:
            if process.poll() is not None:
                cleanup_error = self._cleanup_process_tree_after_root_exit(
                    process,
                    job,
                )
                return not cleanup_error, cleanup_error or terminate_error
            return False, f"Could not kill harvest build process tree: {exc}"
        finally:
            if windows_job_handle is not None:
                self._close_windows_handle(windows_job_handle)
        try:
            process.wait(timeout=self.terminate_timeout_seconds)
            return True, terminate_error
        except subprocess.TimeoutExpired:
            return False, "Harvest build process tree did not exit after forced termination."

    def _cleanup_process_tree_after_root_exit(
        self,
        process: subprocess.Popen[str],
        job: _Job,
    ) -> str:
        try:
            if os.name == "nt":
                self._cleanup_windows_job_after_root_exit(job)
            elif isinstance(process, _REAL_POPEN_TYPE):
                self._signal_process_tree(process, force=True)
        except (ProcessLookupError, PermissionError):
            return ""
        except Exception as exc:
            return (
                "Could not clean up harvest build descendants after the root "
                f"process exited: {exc}"
            )
        return ""

    @staticmethod
    def _signal_process_tree(
        process: subprocess.Popen[str],
        *,
        force: bool,
        windows_job_handle: int | None = None,
    ) -> None:
        """Signal the independent process group, including every stage child."""

        if os.name == "nt":
            if not force:
                process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
                return
            if windows_job_handle is not None:
                HarvestBuildJobManager._terminate_windows_job(windows_job_handle)
                return
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
            if result.returncode != 0 and process.poll() is None:
                process.kill()
            return

        process_group_id = process.pid
        try:
            os.killpg(
                process_group_id,
                getattr(signal, "SIGKILL", 9) if force else signal.SIGINT,
            )
        except ProcessLookupError:
            # Popen-like test doubles (and the narrow real-world race where
            # setsid completed differently than expected) can report a live
            # root process without there being a matching process group.
            # Leaving that root untouched keeps the worker blocked in wait()
            # forever, so fall back to signalling the root itself.
            if process.poll() is not None:
                return
            if force:
                process.kill()
            else:
                process.send_signal(signal.SIGTERM)

    @staticmethod
    def _force_stop_unmanaged_process_tree(process: subprocess.Popen[str]) -> None:
        """Best-effort cleanup if Windows Job Object setup itself fails."""

        try:
            HarvestBuildJobManager._signal_process_tree(process, force=True)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        try:
            process.wait(timeout=2)
        except Exception:
            pass

    @staticmethod
    def _attach_windows_kill_job(process: subprocess.Popen[str]) -> int | None:
        """Assign a Windows child to a kill-on-close Job Object.

        The new process group provides a catchable CTRL_BREAK grace signal;
        the Job Object is the reliable backstop that owns all later stage
        descendants.  Test doubles have no native ``_handle`` and simply use
        the process-group behavior.
        """

        if os.name != "nt" or not hasattr(process, "_handle"):
            return None
        import ctypes
        from ctypes import wintypes

        job_object_extended_limit_information = 9
        job_object_limit_kill_on_job_close = 0x00002000

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        numeric_handle = int(handle)
        information = _ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = (
            job_object_limit_kill_on_job_close
        )
        if not kernel32.SetInformationJobObject(
            handle,
            job_object_extended_limit_information,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            HarvestBuildJobManager._close_windows_handle(numeric_handle)
            raise error
        if not kernel32.AssignProcessToJobObject(
            handle,
            wintypes.HANDLE(int(process._handle)),  # type: ignore[attr-defined]
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            HarvestBuildJobManager._close_windows_handle(numeric_handle)
            raise error
        return numeric_handle

    @staticmethod
    def _terminate_windows_job(handle: int) -> None:
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        if not kernel32.TerminateJobObject(wintypes.HANDLE(handle), 1):
            raise ctypes.WinError(ctypes.get_last_error())

    @staticmethod
    def _close_windows_handle(handle: int) -> None:
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle(wintypes.HANDLE(handle))

    def _cleanup_windows_job_after_root_exit(self, job: _Job) -> None:
        if os.name != "nt":
            return
        handle = self._take_windows_job_handle(job)
        if handle is None:
            return
        try:
            self._terminate_windows_job(handle)
        finally:
            self._close_windows_handle(handle)

    def _take_windows_job_handle(self, job: _Job) -> int | None:
        """Atomically transfer Job Object ownership to exactly one cleanup path."""

        with self._condition:
            handle = job.windows_job_handle
            job.windows_job_handle = None
            return handle

    def _finish_locked(
        self,
        job: _Job,
        status: str,
        *,
        return_code: int | None = None,
        error: str = "",
    ) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"Not a terminal harvest build status: {status}")
        job.status = status
        if return_code is not None:
            job.return_code = return_code
        if error:
            job.error = error
        job.finished_at = _utc_now()
        self._condition.notify_all()

    def _require_job_locked(self, job_id: str | None) -> _Job:
        if self._job is None:
            raise HarvestBuildJobNotFound(job_id)
        if job_id is not None and job_id != self._job.id:
            raise HarvestBuildJobNotFound(job_id)
        return self._job

    def _snapshot_locked(self, job: _Job) -> dict[str, object]:
        return {
            "id": job.id,
            "status": job.status,
            "pid": job.pid,
            "returnCode": job.return_code,
            "createdAt": job.created_at,
            "startedAt": job.started_at,
            "finishedAt": job.finished_at,
            "cancelRequested": job.cancel_requested,
            "cancellationDeferred": job.cancellation_deferred,
            "cancelTooLate": job.cancel_too_late,
            "promotionCritical": job.promotion_critical,
            "promotionCommitted": job.promotion_committed,
            "error": redact_sensitive_text(
                job.error,
                path_roots=self._redaction_roots,
                redact_absolute_paths=True,
            ),
            "progress": dict(job.progress),
            "progressLines": list(job.progress_lines),
            "logTail": job.log_tail,
            "logTruncated": job.log_truncated,
            "logCharLimit": self.max_log_chars,
        }


__all__ = [
    "ACTIVE_STATUSES",
    "CANCELLED",
    "FAILED",
    "HarvestBuildAlreadyRunning",
    "HarvestBuildArgumentError",
    "HarvestBuildJobError",
    "HarvestBuildJobManager",
    "HarvestBuildJobNotFound",
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "TERMINAL_STATUSES",
]
