"""Verify and clean-extract smoke-test a Windows portable release ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from package_full_env import (  # noqa: E402
    ARCHIVE_ROOT,
    _normalized_relative,
    _sha256_file,
    is_safe_archive_path,
)
from package_windows_portable import (  # noqa: E402
    PACKAGE_SCHEMA,
    PACKAGE_TYPE,
    PORTABLE_REQUIRED_FILES,
    _validate_portable_relative_path,
)


def verify_zip_integrity(archive_path: Path) -> dict[str, object]:
    """Validate ZIP paths, manifest identity, and every internal checksum."""

    path = archive_path.expanduser().resolve()
    archive_sha256 = _sha256_file(path)
    with zipfile.ZipFile(path) as archive:
        names = [name.replace("\\", "/") for name in archive.namelist()]
        if len(names) != len(set(names)):
            raise ValueError("portable ZIP contains duplicate paths")
        unsafe = [name for name in names if not is_safe_archive_path(name)]
        if unsafe:
            raise ValueError(f"portable ZIP contains unsafe paths: {unsafe[:5]}")
        manifest_name = f"{ARCHIVE_ROOT}/PACKAGE_MANIFEST.json"
        sums_name = f"{ARCHIVE_ROOT}/SHA256SUMS.txt"
        version_name = f"{ARCHIVE_ROOT}/VERSION"
        required = {manifest_name, sums_name, version_name}
        missing = sorted(required - set(names))
        if missing:
            raise ValueError(f"portable ZIP is missing integrity files: {missing}")

        parsed_hashes: dict[str, str] = {}
        for raw_line in archive.read(sums_name).decode("utf-8").splitlines():
            digest, separator, relative = raw_line.partition("  ")
            archive_name = f"{ARCHIVE_ROOT}/{_normalized_relative(relative)}"
            if (
                not separator
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not is_safe_archive_path(archive_name)
                or archive_name in parsed_hashes
            ):
                raise ValueError(f"invalid portable checksum entry: {raw_line!r}")
            parsed_hashes[archive_name] = digest
        expected_hashed_names = set(names) - {sums_name}
        if set(parsed_hashes) != expected_hashed_names:
            raise ValueError("portable checksum file set differs from ZIP entries")
        for name, expected in parsed_hashes.items():
            actual = hashlib.sha256(archive.read(name)).hexdigest()
            if actual != expected:
                raise ValueError(f"portable checksum mismatch: {name}")

        manifest = json.loads(archive.read(manifest_name))
        if manifest.get("schema") != PACKAGE_SCHEMA:
            raise ValueError("portable manifest schema is invalid")
        if manifest.get("packageType") != PACKAGE_TYPE:
            raise ValueError("portable manifest package type is invalid")
        if manifest.get("platform") != "windows" or manifest.get("architecture") != "x64":
            raise ValueError("portable manifest platform contract is invalid")
        if int(manifest.get("fileCount") or -1) != len(names):
            raise ValueError("portable manifest file count differs from ZIP entries")
        version = archive.read(version_name).decode("utf-8-sig").strip()
        if manifest.get("version") != version:
            raise ValueError("portable manifest version differs from VERSION")
        for name in names:
            relative = name.removeprefix(f"{ARCHIVE_ROOT}/")
            _validate_portable_relative_path(relative)

    return {
        "version": version,
        "entryCount": len(names),
        "sha256": archive_sha256,
        "manifest": manifest,
    }


def verify_external_checksum(archive_path: Path, checksum_path: Path) -> str:
    raw = checksum_path.read_text(encoding="utf-8-sig").strip()
    digest, separator, filename = raw.partition("  ")
    if (
        not separator
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or filename != archive_path.name
    ):
        raise ValueError("portable external checksum file is invalid")
    actual = _sha256_file(archive_path)
    if actual != digest:
        raise ValueError("portable ZIP differs from its external checksum")
    return actual


def _read_http(url: str, *, timeout: float) -> bytes:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"portable HTTP smoke returned {response.status}: {url}")
        return response.read()


def is_built_homepage(content: bytes) -> bool:
    lowered = bytes(content).lower()
    return b"<html" in lowered and b"</html>" in lowered


def _require_default_loopback_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", port))


def _system_root(environment: dict[str, str]) -> Path:
    system_drive = environment.get("SystemDrive") or "C" + ":"
    return Path(
        environment.get("SystemRoot") or str(Path(system_drive + os.sep) / "Windows")
    )


def _portable_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    system_root = _system_root(environment)
    environment["PATH"] = os.pathsep.join(
        (
            str(system_root / "System32"),
            str(system_root / "System32" / "WindowsPowerShell" / "v1.0"),
            str(system_root),
        )
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    return environment


def _stop_process_tree(
    process: subprocess.Popen[str], environment: dict[str, str]
) -> tuple[str, str]:
    if process.poll() is None:
        system_root = _system_root(environment)
        taskkill = system_root / "System32" / "taskkill.exe"
        subprocess.run(
            [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
    stdout, stderr = process.communicate(timeout=5)
    return stdout, stderr


def smoke_archive(archive_path: Path, *, timeout_seconds: float = 30.0) -> dict[str, object]:
    if os.name != "nt":
        raise RuntimeError("Windows portable smoke tests must run on Windows")
    integrity = verify_zip_integrity(archive_path)
    manifest = dict(integrity["manifest"])
    with tempfile.TemporaryDirectory(prefix="蓝图 portable smoke ") as temp_dir:
        extract_root = Path(temp_dir)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extract_root)
        package_root = extract_root / ARCHIVE_ROOT
        missing = [
            relative
            for relative in sorted(PORTABLE_REQUIRED_FILES)
            if not (package_root / relative).is_file()
        ]
        if missing:
            raise ValueError(f"portable extraction is missing required files: {missing}")

        launcher = package_root / "START_HERE.bat"
        port = 8765
        try:
            _require_default_loopback_port(port)
        except OSError as exc:
            raise RuntimeError(
                "portable launcher smoke requires its default loopback port 8765 "
                "to be available"
            ) from exc
        environment = _portable_environment()
        environment["BLUEPRINT_TO_CODE_NO_OPEN"] = "1"
        system_root = _system_root(environment)
        command_processor = Path(
            environment.get("ComSpec") or system_root / "System32" / "cmd.exe"
        )
        process = subprocess.Popen(
            [str(command_processor), "/d", "/c", str(launcher)],
            cwd=package_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        caught: Exception | None = None
        state: dict[str, Any] | None = None
        session: dict[str, Any] | None = None
        homepage = b""
        try:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"portable server exited before readiness: {process.returncode}"
                    )
                try:
                    state = json.loads(
                        _read_http(
                            f"http://127.0.0.1:{port}/api/state",
                            timeout=2.0,
                        )
                    )
                    break
                except (OSError, ValueError, urllib.error.URLError):
                    time.sleep(0.2)
            if state is None:
                raise TimeoutError("portable server did not become ready")
            homepage = _read_http(f"http://127.0.0.1:{port}/", timeout=3.0)
            session = json.loads(
                _read_http(f"http://127.0.0.1:{port}/api/session", timeout=3.0)
            )
            if str(state.get("version") or "") != str(manifest.get("version") or ""):
                raise ValueError("portable API version differs from package manifest")
            if not session.get("ok"):
                raise ValueError("portable session endpoint did not return ok=true")
            if not is_built_homepage(homepage):
                raise ValueError("portable homepage did not return built HTML")
        except Exception as exc:
            caught = exc
        finally:
            stdout, stderr = _stop_process_tree(process, environment)
        if caught is not None:
            raise RuntimeError(
                f"{caught}; server stdout={stdout[-2000:]!r}; "
                f"server stderr={stderr[-2000:]!r}"
            ) from caught

    return {
        "version": manifest["version"],
        "entryCount": integrity["entryCount"],
        "sha256": integrity["sha256"],
        "homeBytes": len(homepage),
        "sessionOk": bool(session and session.get("ok")),
        "host": "127.0.0.1",
        "usedBundledPython": True,
        "usedStartHereLauncher": True,
        "systemPythonAndNodeRemovedFromPath": True,
        "processExited": process.poll() is not None,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify and clean-extract smoke-test a Windows portable ZIP."
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checksum", type=Path)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    archive_path = args.archive.expanduser().resolve()
    checksum_path = (
        args.checksum.expanduser().resolve()
        if args.checksum is not None
        else archive_path.with_suffix(archive_path.suffix + ".sha256")
    )
    try:
        verify_external_checksum(archive_path, checksum_path)
        result = smoke_archive(archive_path, timeout_seconds=args.timeout)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
