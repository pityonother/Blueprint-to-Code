from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from blueprint_tool_server import (  # noqa: E402
    cancel_job,
    create_background_job,
    get_job,
)


TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


def wait_for_job(job_id: str, timeout: float = 8.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = get_job(job_id)
        if str(job.get("status")) in TERMINAL:
            return job
        time.sleep(0.02)
    raise TimeoutError(f"job {job_id} did not finish")


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return completed.returncode == 0
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def force_kill_pid(pid: int) -> None:
    if not pid_exists(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class GenericJobSecurityTests(unittest.TestCase):
    def test_public_job_omits_command_and_redacts_absolute_paths(self) -> None:
        private_path = ROOT / "private-job-output.txt"
        job = create_background_job(
            "test",
            "public snapshot",
            [
                sys.executable,
                "-c",
                f"print({str(private_path)!r})",
            ],
            lambda return_code: {
                "returnCode": return_code,
                "outputPath": str(private_path),
            },
        )
        completed = wait_for_job(str(job["id"]))
        serialized = repr(completed)

        self.assertNotIn("command", completed)
        self.assertNotIn(str(ROOT), serialized)
        self.assertNotIn(str(private_path), serialized)

    def test_public_job_output_is_bounded(self) -> None:
        job = create_background_job(
            "test",
            "bounded output",
            [sys.executable, "-c", "print('x' * 200000)"],
            lambda return_code: {"returnCode": return_code},
        )
        completed = wait_for_job(str(job["id"]))

        self.assertLessEqual(len(str(completed.get("stdout") or "")), 65536)
        self.assertTrue(completed.get("stdoutTruncated"))

    def test_cancel_terminates_spawned_child_process_tree(self) -> None:
        child_pid = 0
        with tempfile.TemporaryDirectory() as temp_dir:
            pid_file = Path(temp_dir) / "child.pid"
            parent_code = "\n".join(
                [
                    "import subprocess, sys, time",
                    "from pathlib import Path",
                    (
                        "child = subprocess.Popen("
                        "[sys.executable, '-c', 'import time; time.sleep(120)'], "
                        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
                        "stderr=subprocess.DEVNULL)"
                    ),
                    f"Path({str(pid_file)!r}).write_text(str(child.pid), encoding='utf-8')",
                    "time.sleep(120)",
                ]
            )
            job = create_background_job(
                "test",
                "tree cancellation",
                [sys.executable, "-c", parent_code],
                lambda return_code: {"returnCode": return_code},
            )
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not pid_file.is_file():
                    time.sleep(0.02)
                self.assertTrue(pid_file.is_file(), "parent did not report its child pid")
                child_pid = int(pid_file.read_text(encoding="utf-8"))
                self.assertTrue(pid_exists(child_pid))

                cancel_job(str(job["id"]))
                completed = wait_for_job(str(job["id"]))
                self.assertEqual(completed["status"], "cancelled")

                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and pid_exists(child_pid):
                    time.sleep(0.05)
                self.assertFalse(
                    pid_exists(child_pid),
                    "generic job cancellation left a spawned child running",
                )
            finally:
                if child_pid:
                    force_kill_pid(child_pid)


if __name__ == "__main__":
    unittest.main()
