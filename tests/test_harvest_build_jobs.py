import io
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import ctypes
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_build_jobs import (  # noqa: E402
    CANCELLED,
    FAILED,
    RUNNING,
    SUCCEEDED,
    HarvestBuildAlreadyRunning,
    HarvestBuildArgumentError,
    HarvestBuildJobManager,
    HarvestBuildJobNotFound,
    _Job,
)


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        return_code: int = 0,
        blocked: bool = False,
        ignore_terminate: bool = False,
        pid: int = 4242,
    ) -> None:
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.pid = pid
        self.returncode: int | None = None
        self._planned_return_code = return_code
        self._done = threading.Event()
        self._ignore_terminate = ignore_terminate
        self.terminate_calls = 0
        self.kill_calls = 0
        self.signal_calls: list[int] = []
        if not blocked:
            self.returncode = return_code
            self._done.set()

    def poll(self) -> int | None:
        return self.returncode if self._done.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired(["fake-build"], timeout)
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        if not self._ignore_terminate:
            self.returncode = -15
            self._done.set()

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self._done.set()

    def send_signal(self, signal_number: int) -> None:
        self.signal_calls.append(signal_number)
        if not self._ignore_terminate:
            self.returncode = -signal_number
            self._done.set()

    def complete(self, return_code: int = 0) -> None:
        self.returncode = return_code
        self._done.set()


class DelayedStream(io.StringIO):
    """Hold stdout so cancellation can race process exit before marker parsing."""

    def __init__(self, value: str, release: threading.Event) -> None:
        super().__init__(value)
        self.release = release

    def readline(self, *args, **kwargs) -> str:
        if not self.release.wait(2):
            raise TimeoutError("delayed test stream was never released")
        return super().readline(*args, **kwargs)


def wait_until_running(manager: HarvestBuildJobManager, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        snapshot = manager.get(job_id)
        if snapshot["status"] == RUNNING and snapshot["pid"] is not None:
            return snapshot
        time.sleep(0.005)
    raise AssertionError(f"job did not enter RUNNING: {manager.get(job_id)}")


def pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(  # type: ignore[attr-defined]
                handle,
                ctypes.byref(exit_code),
            ):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def force_kill_pid(pid: int) -> None:
    if not pid_is_running(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    os.kill(pid, signal.SIGKILL)


class HarvestBuildJobManagerTests(unittest.TestCase):
    def test_construction_does_not_start_a_real_build(self):
        with mock.patch(
            "blueprint_translator.harvest_build_jobs.subprocess.Popen"
        ) as popen:
            manager = HarvestBuildJobManager(project_root=ROOT)

        popen.assert_not_called()
        with self.assertRaises(HarvestBuildJobNotFound):
            manager.get()

    def test_success_exposes_pid_progress_and_bounded_log(self):
        process = FakeProcess(
            stdout=(
                "old output that should be trimmed from the bounded tail\n"
                "[1/6] rank_ark_harvest.py\n"
                "[6/6] verify_ark_harvest_report.py\n"
            ),
            stderr="one warning\n",
            return_code=0,
            pid=7301,
        )
        with mock.patch(
            "blueprint_translator.harvest_build_jobs.subprocess.Popen",
            return_value=process,
        ) as popen:
            manager = HarvestBuildJobManager(
                project_root=ROOT,
                python_executable=ROOT / "fake-python.exe",
                max_log_chars=80,
            )
            accepted = manager.start({"skip_images": True})
            completed = manager.wait(accepted["id"], timeout=2)

        self.assertEqual(accepted["status"], "QUEUED")
        self.assertEqual(completed["status"], SUCCEEDED)
        self.assertEqual(completed["pid"], 7301)
        self.assertEqual(completed["returnCode"], 0)
        self.assertEqual(
            completed["progress"],
            {
                "current": 6,
                "total": 6,
                "label": "verify_ark_harvest_report.py",
                "line": "[6/6] verify_ark_harvest_report.py",
            },
        )
        self.assertEqual(
            completed["progressLines"],
            [
                "[1/6] rank_ark_harvest.py",
                "[6/6] verify_ark_harvest_report.py",
            ],
        )
        self.assertLessEqual(len(completed["logTail"]), 80)
        self.assertTrue(completed["logTruncated"])
        command = completed["command"]
        self.assertIsInstance(command, list)
        self.assertIn("--skip-images", command)
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], command)
        self.assertIs(popen.call_args.kwargs["shell"], False)
        self.assertEqual(popen.call_args.kwargs["cwd"], str(ROOT.resolve()))

    def test_build_process_starts_in_an_independent_native_process_group(self):
        process = FakeProcess()
        with mock.patch(
            "blueprint_translator.harvest_build_jobs.subprocess.Popen",
            return_value=process,
        ) as popen:
            manager = HarvestBuildJobManager(project_root=ROOT)
            accepted = manager.start()
            manager.wait(accepted["id"], timeout=2)

        kwargs = popen.call_args.kwargs
        if os.name == "nt":
            self.assertTrue(
                kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
            )
            self.assertNotIn("start_new_session", kwargs)
        else:
            self.assertIs(kwargs["start_new_session"], True)
            self.assertNotIn("creationflags", kwargs)

    def test_posix_launch_and_tree_signals_use_session_sigint_then_sigkill(self):
        process = FakeProcess()
        manager = HarvestBuildJobManager(project_root=ROOT)
        with mock.patch(
            "blueprint_translator.harvest_build_jobs.os.name",
            "posix",
        ), mock.patch(
            "blueprint_translator.harvest_build_jobs.subprocess.Popen",
            return_value=process,
        ) as popen:
            accepted = manager.start()
            manager.wait(accepted["id"], timeout=2)

        self.assertIs(popen.call_args.kwargs["start_new_session"], True)
        self.assertNotIn("creationflags", popen.call_args.kwargs)

        blocked = FakeProcess(blocked=True, pid=9912)
        with mock.patch(
            "blueprint_translator.harvest_build_jobs.os.name",
            "posix",
        ), mock.patch(
            "blueprint_translator.harvest_build_jobs.os.killpg",
            create=True,
        ) as killpg:
            manager._signal_process_tree(blocked, force=False)
            manager._signal_process_tree(blocked, force=True)

        self.assertEqual(
            killpg.call_args_list,
            [
                mock.call(9912, signal.SIGINT),
                mock.call(9912, getattr(signal, "SIGKILL", 9)),
            ],
        )

    def test_nonzero_exit_is_failed_and_keeps_stderr(self):
        process = FakeProcess(stderr="catalog validation failed\n", return_code=7)
        with mock.patch(
            "blueprint_translator.harvest_build_jobs.subprocess.Popen",
            return_value=process,
        ):
            manager = HarvestBuildJobManager(project_root=ROOT)
            accepted = manager.start()
            completed = manager.wait(accepted["id"], timeout=2)

        self.assertEqual(completed["status"], FAILED)
        self.assertEqual(completed["returnCode"], 7)
        self.assertIn("catalog validation failed", completed["logTail"])
        self.assertIn("code 7", completed["error"])

    def test_only_one_active_build_is_allowed(self):
        process = FakeProcess(blocked=True)
        with mock.patch(
            "blueprint_translator.harvest_build_jobs.subprocess.Popen",
            return_value=process,
        ):
            manager = HarvestBuildJobManager(project_root=ROOT)
            accepted = manager.start()
            wait_until_running(manager, accepted["id"])

            with self.assertRaises(HarvestBuildAlreadyRunning) as raised:
                manager.start()

            self.assertEqual(raised.exception.job_id, accepted["id"])
            cancelled = manager.cancel(accepted["id"])

        self.assertEqual(cancelled["status"], CANCELLED)

    def test_cancel_terminates_then_kills_and_is_idempotent(self):
        process = FakeProcess(blocked=True, ignore_terminate=True, pid=8808)
        with mock.patch(
            "blueprint_translator.harvest_build_jobs.subprocess.Popen",
            return_value=process,
        ), mock.patch(
            "blueprint_translator.harvest_build_jobs.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1),
        ):
            manager = HarvestBuildJobManager(
                project_root=ROOT,
                terminate_timeout_seconds=0.01,
            )
            accepted = manager.start()
            wait_until_running(manager, accepted["id"])

            first = manager.cancel(accepted["id"])
            final = manager.wait(accepted["id"], timeout=2)
            second = manager.cancel(accepted["id"])

        self.assertEqual(first["status"], CANCELLED)
        self.assertEqual(final["status"], CANCELLED)
        self.assertEqual(second["status"], CANCELLED)
        self.assertEqual(process.terminate_calls, 0)
        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(
            process.signal_calls,
            [getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM)],
        )
        self.assertEqual(final["pid"], 8808)

    def test_cancel_during_promotion_is_deferred_and_success_matches_committed_new_bundle(self):
        process = FakeProcess(blocked=True, pid=8820)
        with mock.patch(
            "blueprint_translator.harvest_build_jobs.subprocess.Popen",
            return_value=process,
        ):
            manager = HarvestBuildJobManager(project_root=ROOT)
            accepted = manager.start()
            wait_until_running(manager, accepted["id"])
            assert manager._job is not None
            manager._append_line(
                manager._job,
                "stdout",
                "[promotion-critical] begin\n",
            )

            deferred = manager.cancel(accepted["id"])
            manager._append_line(
                manager._job,
                "stdout",
                "[promotion-critical] commit-complete\n",
            )
            process.complete(0)
            final = manager.wait(accepted["id"], timeout=2)

        self.assertEqual(deferred["status"], RUNNING)
        self.assertTrue(deferred["cancellationDeferred"])
        self.assertEqual(final["status"], SUCCEEDED)
        self.assertTrue(final["cancelRequested"])
        self.assertTrue(final["cancelTooLate"])
        self.assertTrue(final["promotionCommitted"])
        self.assertEqual(process.terminate_calls, 0)
        self.assertEqual(process.kill_calls, 0)
        self.assertEqual(process.signal_calls, [])

    def test_successful_commit_waits_for_delayed_marker_reader_before_terminal_status(self):
        release = threading.Event()
        process = FakeProcess(blocked=True, pid=8821)
        process.stdout = DelayedStream(
            "[promotion-critical] begin\n"
            "[promotion-critical] commit-complete\n"
            "[promotion-critical] end\n",
            release,
        )
        with mock.patch(
            "blueprint_translator.harvest_build_jobs.subprocess.Popen",
            return_value=process,
        ):
            manager = HarvestBuildJobManager(project_root=ROOT)
            accepted = manager.start()
            wait_until_running(manager, accepted["id"])
            process.complete(0)

            result: dict[str, object] = {}
            cancelled = threading.Event()

            def request_cancel() -> None:
                result.update(manager.cancel(accepted["id"]))
                cancelled.set()

            worker = threading.Thread(target=request_cancel)
            worker.start()
            time.sleep(0.05)
            self.assertFalse(cancelled.is_set())
            release.set()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            final = manager.wait(accepted["id"], timeout=2)

        self.assertEqual(result["status"], SUCCEEDED)
        self.assertEqual(final["status"], SUCCEEDED)
        self.assertTrue(final["cancelRequested"])
        self.assertTrue(final["cancelTooLate"])
        self.assertTrue(final["promotionCommitted"])

    def test_real_cancel_mid_promotion_finishes_one_revision_and_reports_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            scripts = project_root / "scripts"
            final_dir = project_root / "final"
            scripts.mkdir()
            final_dir.mkdir()
            final_paths = [final_dir / f"artifact-{index}.txt" for index in range(6)]
            for path in final_paths:
                path.write_text("old-revision", encoding="utf-8")
            (scripts / "build_ark_harvest_explorer.py").write_text(
                "\n".join(
                    (
                        "import time",
                        "from pathlib import Path",
                        "root = Path(__file__).resolve().parents[1]",
                        "paths = sorted((root / 'final').glob('artifact-*.txt'))",
                        "print('[promotion-critical] begin', flush=True)",
                        "for path in paths:",
                        "    temporary = path.with_suffix('.next')",
                        "    temporary.write_text('new-revision', encoding='utf-8')",
                        "    temporary.replace(path)",
                        "    time.sleep(0.08)",
                        "print('[promotion-critical] commit-complete', flush=True)",
                        "print('[promotion-critical] end', flush=True)",
                    )
                ),
                encoding="utf-8",
            )
            manager = HarvestBuildJobManager(
                project_root=project_root,
                python_executable=sys.executable,
            )
            accepted = manager.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                snapshot = manager.get(accepted["id"])
                values = [path.read_text(encoding="utf-8") for path in final_paths]
                if snapshot["promotionCritical"] and "new-revision" in values:
                    break
                time.sleep(0.01)
            else:
                self.fail("build never entered an observable promotion critical section")

            deferred = manager.cancel(accepted["id"])
            final = manager.wait(accepted["id"], timeout=5)
            final_values = [path.read_text(encoding="utf-8") for path in final_paths]

        self.assertEqual(deferred["status"], RUNNING)
        self.assertTrue(deferred["cancellationDeferred"])
        self.assertEqual(final["status"], SUCCEEDED)
        self.assertTrue(final["cancelRequested"])
        self.assertTrue(final["cancelTooLate"])
        self.assertTrue(final["promotionCommitted"])
        self.assertEqual(
            final_values,
            ["new-revision"] * len(final_paths),
        )

    @unittest.skipUnless(os.name == "nt", "Windows Job Object lifecycle only")
    def test_windows_job_cleanup_claims_handle_before_worker_finalizer(self):
        manager = HarvestBuildJobManager(project_root=ROOT)
        job = _Job(id="job-handle-race", command=[])
        job.windows_job_handle = 101
        terminate_started = threading.Event()
        allow_terminate = threading.Event()
        closed_handles: list[int] = []
        cleanup_errors: list[BaseException] = []

        def terminate(handle: int) -> None:
            terminate_started.set()
            self.assertTrue(allow_terminate.wait(timeout=2))
            if handle in closed_handles:
                raise OSError(6, "invalid handle")

        def cleanup() -> None:
            try:
                manager._cleanup_windows_job_after_root_exit(job)
            except BaseException as exc:
                cleanup_errors.append(exc)

        with (
            mock.patch.object(manager, "_terminate_windows_job", side_effect=terminate),
            mock.patch.object(
                manager,
                "_close_windows_handle",
                side_effect=closed_handles.append,
            ),
        ):
            cleanup_thread = threading.Thread(target=cleanup)
            cleanup_thread.start()
            self.assertTrue(terminate_started.wait(timeout=2))

            # Reproduce the worker-finalizer side of the race. Cleanup must
            # already own the handle, otherwise this closes it during use.
            with manager._condition:
                finalizer_handle = job.windows_job_handle
                job.windows_job_handle = None
            if finalizer_handle is not None:
                manager._close_windows_handle(finalizer_handle)

            allow_terminate.set()
            cleanup_thread.join(timeout=2)

        self.assertFalse(cleanup_thread.is_alive())
        self.assertEqual(cleanup_errors, [])
        self.assertEqual(closed_handles, [101])
        self.assertIsNone(job.windows_job_handle)

    def test_cancel_removes_real_spawned_stage_process_tree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            scripts = project_root / "scripts"
            scripts.mkdir()
            child_pid_path = project_root / "stage-child.pid"
            (scripts / "build_ark_harvest_explorer.py").write_text(
                "\n".join(
                    (
                        "import subprocess, sys, time",
                        "from pathlib import Path",
                        "root = Path(__file__).resolve().parents[1]",
                        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])",
                        "(root / 'stage-child.pid').write_text(str(child.pid), encoding='utf-8')",
                        "print('[1/8] spawned-stage-child', flush=True)",
                        "time.sleep(120)",
                    )
                ),
                encoding="utf-8",
            )
            manager = HarvestBuildJobManager(
                project_root=project_root,
                python_executable=sys.executable,
                terminate_timeout_seconds=0.25,
            )
            accepted = manager.start()
            child_pid: int | None = None
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if child_pid_path.is_file():
                        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                        if pid_is_running(child_pid):
                            break
                    time.sleep(0.02)
                self.assertIsNotNone(child_pid)
                assert child_pid is not None
                self.assertTrue(pid_is_running(child_pid))

                cancelled = manager.cancel(accepted["id"])
                self.assertEqual(cancelled["status"], CANCELLED)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and pid_is_running(child_pid):
                    time.sleep(0.02)
                self.assertFalse(
                    pid_is_running(child_pid),
                    "cancel left the spawned stage process running",
                )
            finally:
                if child_pid is not None:
                    force_kill_pid(child_pid)

    def test_options_are_whitelisted_typed_paths_and_never_shell_text(self):
        manager = HarvestBuildJobManager(project_root=ROOT)

        with self.assertRaises(HarvestBuildArgumentError):
            manager.start({"extra_args": [";", "calc.exe"]})
        with self.assertRaises(HarvestBuildArgumentError):
            manager.start({"skip_map_scan": "true"})
        with self.assertRaises(HarvestBuildArgumentError):
            manager.start({"output_dir": ROOT.parent / "outside"})
        with self.assertRaises(HarvestBuildArgumentError):
            manager.start({"devkit_root": "bad\x00path"})

        process = FakeProcess()
        suspicious = "; calc.exe"
        with mock.patch(
            "blueprint_translator.harvest_build_jobs.subprocess.Popen",
            return_value=process,
        ) as popen:
            accepted = manager.start({"creature_file": suspicious})
            completed = manager.wait(accepted["id"], timeout=2)

        command = completed["command"]
        creature_value = command[command.index("--creature-file") + 1]
        self.assertEqual(creature_value, str((ROOT / suspicious).resolve()))
        self.assertNotIn(";", command[:-1])
        self.assertIs(popen.call_args.kwargs["shell"], False)

    def test_each_output_option_is_confined_to_its_analysis_target_domain(self):
        manager = HarvestBuildJobManager(project_root=ROOT)
        forbidden = (
            ("output_dir", ROOT / "README.md"),
            ("catalog_output", ROOT / ".git" / "config"),
            ("scan_cache", ROOT / "README.md"),
            ("map_scan_cache", ROOT / ".git" / "config"),
            ("creature_scan_cache", ROOT / "docs" / "creatures.json"),
            ("image_cache_root", ROOT / "analysis" / "harvest_nodes" / "catalog.json"),
            (
                "catalog_output",
                ROOT / "analysis" / "harvest_nodes" / "harvest_catalog.sqlite",
            ),
            (
                "scan_cache",
                ROOT / "analysis" / "harvest_nodes" / "resource_node_catalog.json",
            ),
        )
        for option, path in forbidden:
            with self.subTest(option=option, path=path), self.assertRaisesRegex(
                HarvestBuildArgumentError,
                f"{option} must stay within its allowed",
            ):
                manager._build_command({option: path})

        allowed = {
            "output_dir": ROOT / "analysis" / "harvest_rankings",
            "catalog_output": ROOT / "analysis" / "harvest_nodes" / "resource_node_catalog.json",
            "scan_cache": ROOT / "analysis" / "harvest_nodes" / "resource_node_scan_cache.json",
            "map_scan_cache": ROOT / "analysis" / "harvest_nodes" / "map_reference_scan_cache.json",
            "creature_scan_cache": ROOT / "analysis" / "harvest_rankings" / "creature_asset_scan_cache.json",
            "image_cache_root": ROOT / "analysis" / "harvest_nodes" / "images",
        }
        command = manager._build_command(allowed)
        for option, flag in (
            ("output_dir", "--output-dir"),
            ("catalog_output", "--catalog-output"),
            ("scan_cache", "--scan-cache"),
            ("map_scan_cache", "--map-scan-cache"),
            ("creature_scan_cache", "--creature-scan-cache"),
            ("image_cache_root", "--image-cache-root"),
        ):
            self.assertEqual(command[command.index(flag) + 1], str(allowed[option]))


if __name__ == "__main__":
    unittest.main()
