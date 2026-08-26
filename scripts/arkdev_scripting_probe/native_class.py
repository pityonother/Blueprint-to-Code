"""Host-side runner for bounded, read-only ARK DevKit native-class reflection."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from arkdev_scripting_probe.contracts import (
    assert_path_free,
    canonical_json,
    semantic_digest,
)


NATIVE_CLASS_RESULT_SCHEMA = (
    "blueprint-to-code.arkdev-native-class-result/v1"
)
NATIVE_CLASS_REQUEST_SCHEMA = (
    "blueprint-to-code.arkdev-native-class-request/v1"
)
NATIVE_CLASS_REQUEST_ENV = "BLUEPRINT_TO_CODE_NATIVE_CLASS_REQUEST"
NATIVE_CLASS_RESULT_ENV = "BLUEPRINT_TO_CODE_NATIVE_CLASS_RESULT"
MAX_NATIVE_CLASS_RESULT_BYTES = 1024 * 1024
DEFAULT_NATIVE_CLASS_TIMEOUT_SECONDS = 180.0

_NATIVE_CLASS_PATH = re.compile(
    r"^/Script/(?P<module>[A-Za-z_][A-Za-z0-9_]*)\."
    r"(?P<class_name>[A-Za-z_][A-Za-z0-9_]*)$"
)
_PROBE_LOCK = threading.Lock()


class NativeClassProbeError(RuntimeError):
    """A stable, user-safe failure from native-class reflection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_native_class_path(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/").strip("\"'")
    if len(text) > 256:
        return ""
    if text.casefold().startswith("/script/"):
        text = "/Script/" + text[8:]
    return text if _NATIVE_CLASS_PATH.fullmatch(text) is not None else ""


def is_native_class_path(value: str) -> bool:
    return bool(normalize_native_class_path(value))


def _require_file(path: Path, code: str, message: str) -> Path:
    candidate = Path(path)
    if not candidate.is_file():
        raise NativeClassProbeError(code, message)
    return candidate


def _validate_list_of_mappings(value: object, field: str) -> None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise NativeClassProbeError(
            "invalid_native_class_result",
            f"ARK DevKit 原生类结果中的 {field} 无效。",
        )
    if len(value) > 128 or any(not isinstance(item, Mapping) for item in value):
        raise NativeClassProbeError(
            "invalid_native_class_result",
            f"ARK DevKit 原生类结果中的 {field} 超出边界。",
        )


def validate_native_class_result(
    value: object,
    *,
    expected_class_path: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise NativeClassProbeError(
            "invalid_native_class_result",
            "ARK DevKit 原生类反射没有返回有效对象。",
        )
    if value.get("schema") != NATIVE_CLASS_RESULT_SCHEMA:
        raise NativeClassProbeError(
            "invalid_native_class_result",
            "ARK DevKit 原生类反射结果版本不受支持。",
        )
    if value.get("assetPath") != expected_class_path:
        raise NativeClassProbeError(
            "invalid_native_class_result",
            "ARK DevKit 原生类反射返回了不同的类路径。",
        )
    if value.get("sourceKind") != "native_class_reflection":
        raise NativeClassProbeError(
            "invalid_native_class_result",
            "ARK DevKit 原生类反射来源标记无效。",
        )
    if value.get("readOnly") is not True:
        raise NativeClassProbeError(
            "invalid_native_class_result",
            "ARK DevKit 原生类反射未声明只读。",
        )
    if value.get("runtimeStateAvailable") is not False:
        raise NativeClassProbeError(
            "invalid_native_class_result",
            "类反射结果不得冒充在线玩家实时状态。",
        )
    _validate_list_of_mappings(value.get("properties"), "properties")
    _validate_list_of_mappings(value.get("functions"), "functions")
    digest = value.get("semanticDigest")
    if not isinstance(digest, str) or digest != semantic_digest(value):
        raise NativeClassProbeError(
            "invalid_native_class_result",
            "ARK DevKit 原生类反射结果摘要校验失败。",
        )
    try:
        assert_path_free(value)
    except ValueError as exc:
        raise NativeClassProbeError(
            "invalid_native_class_result",
            "ARK DevKit 原生类反射结果包含本机路径。",
        ) from exc
    return value


def _commandlet_paths(devkit_root: Path) -> tuple[Path, Path]:
    root = Path(devkit_root)
    executable = _require_file(
        root / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe",
        "devkit_commandlet_not_found",
        "没有找到 ARK DevKit 的 UnrealEditor-Cmd.exe。",
    )
    project = _require_file(
        root / "Projects" / "ShooterGame" / "ShooterGame.uproject",
        "devkit_project_not_found",
        "没有找到 ARK DevKit 的 ShooterGame.uproject。",
    )
    return executable, project


def run_native_class_probe(
    class_path: str,
    *,
    devkit_root: Path,
    probe_script: Path,
    timeout_seconds: float = DEFAULT_NATIVE_CLASS_TIMEOUT_SECONDS,
    process_runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    """Run one official PythonScriptCommandlet read and validate its result."""

    normalized = normalize_native_class_path(class_path)
    if not normalized:
        raise NativeClassProbeError(
            "invalid_native_class_path",
            "请输入 /Script/模块.类名 格式的原生类路径。",
        )
    executable, project = _commandlet_paths(Path(devkit_root))
    script = _require_file(
        Path(probe_script),
        "native_class_probe_script_not_found",
        "新版缺少原生类只读反射脚本，请重新安装完整发布包。",
    )
    timeout = max(10.0, min(float(timeout_seconds), 600.0))

    with _PROBE_LOCK, tempfile.TemporaryDirectory(
        prefix="blueprint-to-code-native-class-"
    ) as temporary:
        temporary_root = Path(temporary)
        request_path = temporary_root / "request.json"
        result_path = temporary_root / "result.json"
        log_path = temporary_root / "commandlet.log"
        request_path.write_text(
            canonical_json(
                {
                    "schema": NATIVE_CLASS_REQUEST_SCHEMA,
                    "classPath": normalized,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment[NATIVE_CLASS_REQUEST_ENV] = str(request_path)
        environment[NATIVE_CLASS_RESULT_ENV] = str(result_path)
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [
            str(executable),
            str(project),
            "-run=pythonscript",
            f"-script={script}",
            "-unattended",
            "-nop4",
            "-nosplash",
            "-nullrhi",
            "-NoSound",
            "-stdout",
            "-FullStdOutLogOutput",
            f"-abslog={log_path}",
        ]
        run_options: dict[str, object] = {
            "cwd": str(devkit_root),
            "env": environment,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "timeout": timeout,
            "check": False,
        }
        creation_flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if creation_flag:
            run_options["creationflags"] = creation_flag
        try:
            completed = process_runner(command, **run_options)
        except subprocess.TimeoutExpired as exc:
            raise NativeClassProbeError(
                "native_class_probe_timed_out",
                "ARK DevKit 原生类反射超时，请确认 DevKit 没有被更新或占用后重试。",
            ) from exc
        except OSError as exc:
            raise NativeClassProbeError(
                "native_class_probe_start_failed",
                "ARK DevKit 原生类反射进程无法启动。",
            ) from exc

        if int(getattr(completed, "returncode", -1)) != 0:
            raise NativeClassProbeError(
                "native_class_probe_failed",
                "ARK DevKit 原生类反射执行失败，请检查 DevKit 安装完整性。",
            )
        if not result_path.is_file():
            raise NativeClassProbeError(
                "native_class_probe_no_result",
                "ARK DevKit 原生类反射完成但没有产生结果。",
            )
        try:
            size = result_path.stat().st_size
            if size <= 0 or size > MAX_NATIVE_CLASS_RESULT_BYTES:
                raise ValueError("invalid result size")
            payload = json.loads(result_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise NativeClassProbeError(
                "invalid_native_class_result",
                "ARK DevKit 原生类反射结果无法读取。",
            ) from exc
        return validate_native_class_result(
            payload,
            expected_class_path=normalized,
        )


__all__ = [
    "DEFAULT_NATIVE_CLASS_TIMEOUT_SECONDS",
    "NATIVE_CLASS_REQUEST_ENV",
    "NATIVE_CLASS_REQUEST_SCHEMA",
    "NATIVE_CLASS_RESULT_ENV",
    "NATIVE_CLASS_RESULT_SCHEMA",
    "NativeClassProbeError",
    "is_native_class_path",
    "normalize_native_class_path",
    "run_native_class_probe",
    "validate_native_class_result",
]
