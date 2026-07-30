"""Reparse-safe, same-volume staging for one immutable KB snapshot.

The copier uses parent-relative, no-follow opens.  Windows traversal is rooted
in directory handles and uses ``NtCreateFile`` plus handle-based enumeration;
POSIX traversal uses ``dir_fd`` and ``O_NOFOLLOW``.  No staged path is a
publication destination.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final


STAGING_RECEIPT_SCHEMA: Final = (
    "ark-kb-reparse-safe-staging-receipt/v1"
)
STAGING_EVIDENCE_CLASS: Final = (
    "UNSIGNED_LOCAL_REPARSE_SAFE_STAGING"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_READ_BYTES = 1024 * 1024
_SQLITE_SIDECAR = re.compile(r".+\.sqlite-(?:wal|shm)$", re.IGNORECASE)


class SafeStagingError(RuntimeError):
    """A stable staging failure with no publication authority."""

    status = "BLOCKED_GAP"

    def __init__(
        self,
        gap_code: str,
        message: str,
        *,
        status: str = "BLOCKED_GAP",
        residual_identifier: str = "",
    ) -> None:
        super().__init__(message)
        self.gap_code = gap_code
        self.status = status
        self.residual_identifier = residual_identifier


@dataclass(frozen=True)
class SafeStagedSnapshot:
    base_build_id: str
    staging_id: str
    temporary_root: Path
    snapshot_dir: Path
    manifest_sha256: str
    copied_files: int
    receipt: dict[str, object]
    cleanup_identity: tuple[object, ...]


@dataclass(frozen=True)
class SafeCopiedArtifact:
    relative: str
    sha256: str
    size_bytes: int
    file_identity_sha256: str


@dataclass(frozen=True)
class SafeFrozenBlueprintBundle:
    staging_id: str
    source_id: str
    quarantine_root: Path
    bundle_root: Path
    artifacts: tuple[SafeCopiedArtifact, ...]
    quarantine_tree_digest: str
    quarantine_identity: tuple[object, ...]


@dataclass(frozen=True)
class SafeValidatedBlueprintBundle:
    quarantine_tree_digest: str
    artifacts: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True)
class SafeBoundFileObservation:
    """Content and opaque filesystem identity from one no-follow open."""

    relative_path: str
    raw_sha256: str
    size_bytes: int
    file_identity_sha256: str
    volume_identity_sha256: str


@dataclass
class _Entry:
    relative: str
    name: str
    parent_relative: str
    is_dir: bool
    handle: int
    identity: tuple[object, ...]
    size: int
    links: int
    change_marker: tuple[int, ...]
    sha256: str = ""


@dataclass
class _StagingState:
    snapshot_root: Path
    staging_root: Path
    staging_id: str = ""
    relative_identifier: str = ""
    temporary_root: Path | None = None
    snapshot_dir: Path | None = None
    platform: str = field(
        default_factory=lambda: "windows" if os.name == "nt" else "posix"
    )
    source_entries: list[_Entry] = field(default_factory=list)
    destination_entries: list[_Entry] = field(default_factory=list)
    source_directories: dict[str, int] = field(default_factory=dict)
    destination_directories: dict[str, int] = field(default_factory=dict)
    root_chain_handles: list[int] = field(default_factory=list)
    staging_handle: int | None = None
    temporary_handle: int | None = None
    snapshot_handle: int | None = None
    sqlite_connections: list[sqlite3.Connection] = field(default_factory=list)


def _error(code: str, message: str) -> SafeStagingError:
    return SafeStagingError(code, message)


def _strict_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _strict_manifest(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise _error(
            "STAGING_BASELINE_CHANGED",
            "the locked snapshot manifest is not strict UTF-8 JSON",
        ) from exc
    if not isinstance(value, dict):
        raise _error(
            "STAGING_BASELINE_CHANGED",
            "the locked snapshot manifest must be an object",
        )
    return value


def _relative_path(value: object, *, label: str) -> str:
    text = str(value or "")
    path = PurePosixPath(text)
    if (
        not text
        or "\\" in text
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != text
    ):
        raise _error(
            "STAGING_BASELINE_CHANGED",
            f"{label} is not a controlled relative POSIX path",
        )
    return text


def _declared_artifacts(
    manifest: Mapping[str, object],
    *,
    expected_build_id: str,
    expected_manifest_sha256: str,
) -> tuple[dict[str, tuple[int, str]], set[str], str]:
    if (
        manifest.get("schema") != "ark-kb-vnext-snapshot/v1"
        or manifest.get("buildId") != expected_build_id
    ):
        raise _error(
            "STAGING_BASELINE_CHANGED",
            "the locked manifest identity no longer matches the baseline",
        )
    databases = manifest.get("databases")
    if not isinstance(databases, Mapping) or not databases:
        raise _error(
            "STAGING_BASELINE_CHANGED",
            "the snapshot database authority set is missing",
        )
    declared: dict[str, tuple[int, str]] = {
        "manifest.json": (-1, expected_manifest_sha256)
    }
    authority = {"manifest.json"}
    cache_disposition = "ABSENT"
    for raw_name, raw_metrics in databases.items():
        name = _relative_path(raw_name, label="database artifact")
        if not isinstance(raw_metrics, Mapping):
            raise _error(
                "STAGING_BASELINE_CHANGED",
                f"database declaration is invalid: {name}",
            )
        size = raw_metrics.get("bytes")
        digest = str(raw_metrics.get("sha256") or "").lower()
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not _SHA256.fullmatch(digest)
            or name in declared
            or not name.casefold().endswith(".sqlite")
        ):
            raise _error(
                "STAGING_BASELINE_CHANGED",
                f"database identity is invalid: {name}",
            )
        declared[name] = (size, digest)
        if name == "cache.sqlite":
            cache_disposition = "COPIED_BUILD_BOUND_DISPOSABLE"
        else:
            authority.add(name)

    quality = manifest.get("qualityGates")
    cutover = manifest.get("cutover")
    if (
        not isinstance(quality, Mapping)
        or quality.get("sealedInSnapshotManifest") is not True
        or quality.get("cutoverEligible") is not False
        or not isinstance(cutover, Mapping)
        or cutover.get("mode") != "shadow"
        or cutover.get("defaultQuerySource") != "legacy"
    ):
        raise _error(
            "STAGING_BASELINE_CHANGED",
            "the sealed shadow/legacy snapshot contract is invalid",
        )
    report_pairs = (
        ("reportUri", "sha256"),
        ("benchmarkUri", "benchmarkSha256"),
        ("caseResultsUri", "caseResultsSha256"),
        ("failureMatrixUri", "failureMatrixSha256"),
    )
    for path_key, sha_key in report_pairs:
        if path_key not in quality and sha_key not in quality:
            continue
        name = _relative_path(
            quality.get(path_key),
            label="sealed report",
        )
        digest = str(quality.get(sha_key) or "").lower()
        if not _SHA256.fullmatch(digest) or name in declared:
            raise _error(
                "STAGING_BASELINE_CHANGED",
                f"sealed report identity is invalid: {name}",
            )
        declared[name] = (-1, digest)
        authority.add(name)

    burn_in = manifest.get("burnIn")
    if isinstance(burn_in, Mapping) and "reportUri" in burn_in:
        name = _relative_path(
            burn_in.get("reportUri"),
            label="sealed burn-in report",
        )
        digest = str(burn_in.get("sha256") or "").lower()
        if not _SHA256.fullmatch(digest) or name in declared:
            raise _error(
                "STAGING_BASELINE_CHANGED",
                "sealed burn-in report identity is invalid",
            )
        declared[name] = (-1, digest)
        authority.add(name)
    return declared, authority, cache_disposition


if os.name == "nt":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")

    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _FILE_ATTRIBUTE_DEVICE = 0x00000040
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_LIST_DIRECTORY = 0x0001
    _FILE_READ_ATTRIBUTES = 0x0080
    _FILE_ADD_FILE = 0x0002
    _FILE_ADD_SUBDIRECTORY = 0x0004
    _DELETE = 0x00010000
    _SYNCHRONIZE = 0x00100000
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _FILE_SHARE_DELETE = 0x00000004
    _OPEN_EXISTING = 3
    _FILE_OPEN = 1
    _FILE_CREATE = 2
    _FILE_OPEN_IF = 3
    _FILE_DIRECTORY_FILE = 0x00000001
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _FILE_OPEN_FOR_BACKUP_INTENT = 0x00004000
    _FILE_OPEN_REPARSE_POINT = 0x00200000
    _OBJ_CASE_INSENSITIVE = 0x00000040
    _OBJ_DONT_REPARSE = 0x00001000
    _FILE_ID_BOTH_DIRECTORY_INFO = 10
    _FILE_ID_BOTH_DIRECTORY_RESTART_INFO = 11
    _FILE_ID_INFO_CLASS = 18
    _FILE_DISPOSITION_INFO_CLASS = 4
    _ERROR_NO_MORE_FILES = 18
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class _OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_UNICODE_STRING)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class _IO_STATUS_UNION(ctypes.Union):
        _fields_ = [
            ("Status", wintypes.LONG),
            ("Pointer", wintypes.LPVOID),
        ]

    class _IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [
            ("value", _IO_STATUS_UNION),
            ("Information", ctypes.c_size_t),
        ]

    class _FILE_ID_128(ctypes.Structure):
        _fields_ = [("Identifier", ctypes.c_ubyte * 16)]

    class _FILE_ID_INFO(ctypes.Structure):
        _fields_ = [
            ("VolumeSerialNumber", ctypes.c_ulonglong),
            ("FileId", _FILE_ID_128),
        ]

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    class _FILE_ID_BOTH_DIR_INFO(ctypes.Structure):
        _fields_ = [
            ("NextEntryOffset", wintypes.DWORD),
            ("FileIndex", wintypes.DWORD),
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("EndOfFile", ctypes.c_longlong),
            ("AllocationSize", ctypes.c_longlong),
            ("FileAttributes", wintypes.DWORD),
            ("FileNameLength", wintypes.DWORD),
            ("EaSize", wintypes.DWORD),
            ("ShortNameLength", ctypes.c_byte),
            ("ShortName", wintypes.WCHAR * 12),
            ("FileId", ctypes.c_longlong),
            ("FileName", wintypes.WCHAR * 1),
        ]

    class _FILE_DISPOSITION_INFO(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    _kernel32.WriteFile.restype = wintypes.BOOL
    _kernel32.SetFilePointerEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    ]
    _kernel32.SetFilePointerEx.restype = wintypes.BOOL
    _kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _kernel32.FlushFileBuffers.restype = wintypes.BOOL
    _kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    _ntdll.NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(_OBJECT_ATTRIBUTES),
        ctypes.POINTER(_IO_STATUS_BLOCK),
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
    ]
    _ntdll.NtCreateFile.restype = wintypes.LONG
    _ntdll.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
    _ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG


def _win_close(handle: int | None) -> None:
    if (
        os.name == "nt"
        and handle is not None
        and handle != _INVALID_HANDLE_VALUE
    ):
        _kernel32.CloseHandle(handle)


def _win_error(message: str) -> OSError:
    return ctypes.WinError(ctypes.get_last_error(), message)


def _win_info(handle: int) -> _BY_HANDLE_FILE_INFORMATION:
    value = _BY_HANDLE_FILE_INFORMATION()
    if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(value)):
        raise _win_error("GetFileInformationByHandle failed")
    return value


def _win_file_identity(handle: int) -> tuple[object, ...]:
    value = _FILE_ID_INFO()
    if not _kernel32.GetFileInformationByHandleEx(
        handle,
        _FILE_ID_INFO_CLASS,
        ctypes.byref(value),
        ctypes.sizeof(value),
    ):
        raise _win_error("FileIdInfo query failed")
    return (
        int(value.VolumeSerialNumber),
        bytes(value.FileId.Identifier),
    )


def _win_entry(handle: int, relative: str, name: str) -> _Entry:
    info = _win_info(handle)
    attributes = int(info.dwFileAttributes)
    if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise _error(
            "STAGING_REPARSE_POINT_REJECTED",
            f"snapshot entry is a reparse point: {relative}",
        )
    is_dir = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
    if not is_dir and attributes & _FILE_ATTRIBUTE_DEVICE:
        raise _error(
            "STAGING_SPECIAL_FILE_REJECTED",
            f"snapshot entry is a device: {relative}",
        )
    size = (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow)
    write_time = (
        int(info.ftLastWriteTime.dwHighDateTime) << 32
    ) | int(info.ftLastWriteTime.dwLowDateTime)
    return _Entry(
        relative=relative,
        name=name,
        parent_relative=(
            PurePosixPath(relative).parent.as_posix()
            if "/" in relative
            else ""
        ),
        is_dir=is_dir,
        handle=handle,
        identity=_win_file_identity(handle),
        size=size,
        links=int(info.nNumberOfLinks),
        change_marker=(write_time, size, int(info.nNumberOfLinks)),
    )


def _win_open_absolute_chain(path: Path) -> list[int]:
    absolute = Path(os.path.abspath(path))
    anchor = absolute.anchor
    if not anchor:
        raise OSError("absolute Windows path is required")
    handle = _kernel32.CreateFileW(
        anchor,
        _FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise _win_error("failed to open volume root")
    handles = [int(handle)]
    try:
        root_entry = _win_entry(int(handle), anchor, anchor)
        if not root_entry.is_dir:
            raise OSError("volume root is not a directory")
        for part in absolute.parts[1:]:
            child = _win_open_child(
                handles[-1],
                part,
                directory=True,
                create=False,
                writable=False,
            )
            handles.append(child)
        return handles
    except Exception:
        for value in reversed(handles):
            _win_close(value)
        raise


def _win_open_child(
    parent: int,
    name: str,
    *,
    directory: bool,
    create: bool,
    writable: bool,
    open_if: bool = False,
    share_delete: bool = False,
) -> int:
    if not name or name in {".", ".."} or "\\" in name or "/" in name:
        raise OSError("unsafe relative Windows component")
    buffer = ctypes.create_unicode_buffer(name)
    string = _UNICODE_STRING(
        Length=len(name.encode("utf-16-le")),
        MaximumLength=(len(name) + 1) * 2,
        Buffer=ctypes.cast(buffer, wintypes.LPWSTR),
    )
    attributes = _OBJECT_ATTRIBUTES(
        Length=ctypes.sizeof(_OBJECT_ATTRIBUTES),
        RootDirectory=parent,
        ObjectName=ctypes.pointer(string),
        Attributes=_OBJ_CASE_INSENSITIVE | _OBJ_DONT_REPARSE,
        SecurityDescriptor=None,
        SecurityQualityOfService=None,
    )
    status_block = _IO_STATUS_BLOCK()
    handle = wintypes.HANDLE()
    access = _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
    if directory:
        access |= _FILE_LIST_DIRECTORY
        if writable:
            access |= _FILE_ADD_FILE | _FILE_ADD_SUBDIRECTORY | _DELETE
    else:
        access |= _GENERIC_WRITE | _DELETE if writable else _GENERIC_READ
        if writable:
            access |= _GENERIC_READ
    disposition = (
        _FILE_OPEN_IF if open_if else (_FILE_CREATE if create else _FILE_OPEN)
    )
    options = (
        _FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE
    )
    options |= (
        _FILE_SYNCHRONOUS_IO_NONALERT
        | _FILE_OPEN_FOR_BACKUP_INTENT
        | _FILE_OPEN_REPARSE_POINT
    )
    status = int(
        _ntdll.NtCreateFile(
            ctypes.byref(handle),
            access,
            ctypes.byref(attributes),
            ctypes.byref(status_block),
            None,
            0,
            _FILE_SHARE_READ
            | (
                _FILE_SHARE_WRITE
                if directory or share_delete
                else 0
            )
            | (_FILE_SHARE_DELETE if share_delete else 0),
            disposition,
            options,
            None,
            0,
        )
    )
    if status < 0:
        error = int(_ntdll.RtlNtStatusToDosError(status))
        raise OSError(error, os.strerror(error), name)
    value = int(handle.value)
    try:
        entry = _win_entry(value, name, name)
        if entry.is_dir != directory:
            raise OSError("opened Windows child has the wrong type")
        return value
    except Exception:
        _win_close(value)
        raise


def _win_names(directory_handle: int) -> list[str]:
    names: list[str] = []
    first = True
    while True:
        buffer = ctypes.create_string_buffer(64 * 1024)
        info_class = (
            _FILE_ID_BOTH_DIRECTORY_RESTART_INFO
            if first
            else _FILE_ID_BOTH_DIRECTORY_INFO
        )
        first = False
        if not _kernel32.GetFileInformationByHandleEx(
            directory_handle,
            info_class,
            buffer,
            len(buffer),
        ):
            error = ctypes.get_last_error()
            if error == _ERROR_NO_MORE_FILES:
                break
            raise ctypes.WinError(error)
        offset = 0
        while True:
            entry = _FILE_ID_BOTH_DIR_INFO.from_buffer(buffer, offset)
            name = ctypes.wstring_at(
                ctypes.addressof(buffer)
                + offset
                + _FILE_ID_BOTH_DIR_INFO.FileName.offset,
                int(entry.FileNameLength) // 2,
            )
            if name not in {".", ".."}:
                names.append(name)
            next_offset = int(entry.NextEntryOffset)
            if not next_offset:
                break
            offset += next_offset
    if len(names) != len(set(names)):
        raise OSError("directory enumeration returned duplicate names")
    return sorted(names)


def _win_walk(
    root_handle: int,
    *,
    writable: bool = False,
    share_delete: bool = False,
) -> tuple[list[_Entry], dict[str, int]]:
    entries: list[_Entry] = []
    directories = {"": root_handle}

    def visit(relative: str, directory_handle: int) -> None:
        for name in _win_names(directory_handle):
            child_relative = f"{relative}/{name}" if relative else name
            handle: int | None = None
            try:
                try:
                    handle = _win_open_child(
                        directory_handle,
                        name,
                        directory=True,
                        create=False,
                        writable=writable,
                        share_delete=share_delete,
                    )
                except OSError:
                    handle = _win_open_child(
                        directory_handle,
                        name,
                        directory=False,
                        create=False,
                        writable=writable,
                        share_delete=share_delete,
                    )
                entry = _win_entry(handle, child_relative, name)
                entries.append(entry)
                if entry.is_dir:
                    directories[child_relative] = handle
                    visit(child_relative, handle)
            except Exception:
                if handle is not None and not any(
                    item.handle == handle for item in entries
                ):
                    _win_close(handle)
                raise

    try:
        visit("", root_handle)
        return entries, directories
    except Exception:
        _close_entries(entries, keep_directories={root_handle})
        raise


def _win_read_chunks(handle: int):
    if not _kernel32.SetFilePointerEx(handle, 0, None, 0):
        raise _win_error("failed to rewind file handle")
    while True:
        buffer = ctypes.create_string_buffer(_READ_BYTES)
        read = wintypes.DWORD()
        if not _kernel32.ReadFile(
            handle,
            buffer,
            len(buffer),
            ctypes.byref(read),
            None,
        ):
            raise _win_error("ReadFile failed")
        if not read.value:
            break
        yield bytes(buffer.raw[: read.value])


def _win_write(handle: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = wintypes.DWORD()
        chunk = data[offset:]
        buffer = ctypes.create_string_buffer(chunk)
        if not _kernel32.WriteFile(
            handle,
            buffer,
            len(chunk),
            ctypes.byref(written),
            None,
        ):
            raise _win_error("WriteFile failed")
        if not written.value:
            raise OSError("WriteFile made no progress")
        offset += int(written.value)


def _win_mark_delete(handle: int) -> None:
    value = _FILE_DISPOSITION_INFO(DeleteFile=1)
    if not _kernel32.SetFileInformationByHandle(
        handle,
        _FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(value),
        ctypes.sizeof(value),
    ):
        raise _win_error("handle-based staging cleanup failed")


def _posix_open_absolute_chain(path: Path) -> list[int]:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute():
        raise OSError("absolute POSIX path is required")
    flags = os.O_RDONLY | os.O_DIRECTORY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    handles = [os.open("/", flags)]
    try:
        for part in absolute.parts[1:]:
            handles.append(os.open(part, flags, dir_fd=handles[-1]))
        return handles
    except Exception:
        for handle in reversed(handles):
            os.close(handle)
        raise


def _posix_entry(
    handle: int,
    relative: str,
    name: str,
) -> _Entry:
    value = os.fstat(handle)
    if stat.S_ISLNK(value.st_mode):
        raise _error(
            "STAGING_REPARSE_POINT_REJECTED",
            f"snapshot entry is a symlink: {relative}",
        )
    is_dir = stat.S_ISDIR(value.st_mode)
    if not is_dir and not stat.S_ISREG(value.st_mode):
        raise _error(
            "STAGING_SPECIAL_FILE_REJECTED",
            f"snapshot entry is not a regular file: {relative}",
        )
    return _Entry(
        relative=relative,
        name=name,
        parent_relative=(
            PurePosixPath(relative).parent.as_posix()
            if "/" in relative
            else ""
        ),
        is_dir=is_dir,
        handle=handle,
        identity=(int(value.st_dev), int(value.st_ino)),
        size=int(value.st_size),
        links=int(value.st_nlink),
        change_marker=(
            int(value.st_mtime_ns),
            int(value.st_ctime_ns),
            int(value.st_size),
            int(value.st_nlink),
        ),
    )


def _posix_walk(root_handle: int) -> tuple[list[_Entry], dict[str, int]]:
    entries: list[_Entry] = []
    directories = {"": root_handle}
    directory_flags = os.O_RDONLY | os.O_DIRECTORY
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)

    def visit(relative: str, directory_handle: int) -> None:
        for name in sorted(os.listdir(directory_handle)):
            before = os.stat(
                name,
                dir_fd=directory_handle,
                follow_symlinks=False,
            )
            child_relative = f"{relative}/{name}" if relative else name
            if stat.S_ISLNK(before.st_mode):
                raise _error(
                    "STAGING_REPARSE_POINT_REJECTED",
                    f"snapshot entry is a symlink: {child_relative}",
                )
            if stat.S_ISDIR(before.st_mode):
                flags = directory_flags
            elif stat.S_ISREG(before.st_mode):
                flags = file_flags
            else:
                raise _error(
                    "STAGING_SPECIAL_FILE_REJECTED",
                    "snapshot entry is not a regular file: "
                    + child_relative,
                )
            handle = os.open(name, flags, dir_fd=directory_handle)
            try:
                entry = _posix_entry(handle, child_relative, name)
                if entry.identity != (
                    int(before.st_dev),
                    int(before.st_ino),
                ):
                    raise _error(
                        "STAGING_SOURCE_IDENTITY_CHANGED",
                        f"snapshot entry changed before open: {child_relative}",
                    )
                entries.append(entry)
                if entry.is_dir:
                    directories[child_relative] = handle
                    visit(child_relative, handle)
            except Exception:
                if not any(item.handle == handle for item in entries):
                    os.close(handle)
                raise

    try:
        visit("", root_handle)
        return entries, directories
    except Exception:
        _close_entries(entries, keep_directories={root_handle})
        raise


def _handle_identity(handle: int) -> tuple[object, ...]:
    if os.name == "nt":
        return _win_file_identity(handle)
    value = os.fstat(handle)
    return int(value.st_dev), int(value.st_ino)


def _volume_identity(handle_or_fd: int) -> object:
    """Return a stable volume identity; kept patchable for cross-volume tests."""

    if os.name == "nt":
        return _win_file_identity(handle_or_fd)[0]
    return int(os.fstat(handle_or_fd).st_dev)


def _read_chunks(handle: int):
    if os.name == "nt":
        yield from _win_read_chunks(handle)
        return
    os.lseek(handle, 0, os.SEEK_SET)
    while chunk := os.read(handle, _READ_BYTES):
        yield chunk


def _hash_handle(handle: int) -> str:
    digest = hashlib.sha256()
    for chunk in _read_chunks(handle):
        digest.update(chunk)
    return digest.hexdigest()


def _close_handle(handle: int) -> None:
    if os.name == "nt":
        _win_close(handle)
    else:
        os.close(handle)


def _close_entries(
    entries: list[_Entry],
    *,
    keep_directories: set[int] | None = None,
) -> None:
    keep = keep_directories or set()
    closed: set[int] = set()
    for entry in reversed(entries):
        if (
            entry.handle < 0
            or entry.handle in closed
            or entry.handle in keep
        ):
            continue
        _close_handle(entry.handle)
        closed.add(entry.handle)
        entry.handle = -1


def _close_file_entries(entries: list[_Entry]) -> None:
    for entry in entries:
        if not entry.is_dir and entry.handle >= 0:
            _close_handle(entry.handle)
            entry.handle = -1


def _walk(
    root_handle: int,
    *,
    writable: bool = False,
    share_delete: bool = False,
) -> tuple[list[_Entry], dict[str, int]]:
    if os.name == "nt":
        return _win_walk(
            root_handle,
            writable=writable,
            share_delete=share_delete,
        )
    return _posix_walk(root_handle)


def _open_source_root(state: _StagingState, build_id: str) -> int:
    if os.name == "nt":
        state.root_chain_handles = _win_open_absolute_chain(
            state.snapshot_root
        )
        output_handle = state.root_chain_handles[-1]
        snapshots = _win_open_child(
            output_handle,
            "snapshots",
            directory=True,
            create=False,
            writable=False,
        )
        state.root_chain_handles.append(snapshots)
        source = _win_open_child(
            snapshots,
            build_id,
            directory=True,
            create=False,
            writable=False,
        )
        state.root_chain_handles.append(source)
        return source
    state.root_chain_handles = _posix_open_absolute_chain(
        state.snapshot_root
    )
    output_handle = state.root_chain_handles[-1]
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    snapshots = os.open("snapshots", flags, dir_fd=output_handle)
    state.root_chain_handles.append(snapshots)
    source = os.open(build_id, flags, dir_fd=snapshots)
    state.root_chain_handles.append(source)
    return source


def _open_existing_directory(
    parent_handle: int,
    name: str,
) -> int:
    if os.name == "nt":
        return _win_open_child(
            parent_handle,
            name,
            directory=True,
            create=False,
            writable=False,
            share_delete=True,
        )
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0)
    )
    return os.open(name, flags, dir_fd=parent_handle)


def _validate_source_root_binding(
    state: _StagingState,
    *,
    build_id: str,
    source_root: int,
) -> None:
    observed: int | None = None
    try:
        observed = _open_existing_directory(
            state.root_chain_handles[-2],
            build_id,
        )
        if _handle_identity(observed) != _handle_identity(source_root):
            raise OSError("source root identity changed")
    except Exception as exc:
        raise _error(
            "STAGING_SOURCE_IDENTITY_CHANGED",
            "the source root name no longer identifies the pinned directory",
        ) from exc
    finally:
        if observed is not None:
            _close_handle(observed)


def _validate_destination_root_binding(
    state: _StagingState,
) -> None:
    if (
        state.staging_handle is None
        or state.temporary_handle is None
        or state.snapshot_handle is None
    ):
        raise _error(
            "STAGING_DESTINATION_IDENTITY_CHANGED",
            "the staging destination handles are incomplete",
        )
    temporary: int | None = None
    snapshot: int | None = None
    try:
        temporary = _open_existing_directory(
            state.staging_handle,
            state.staging_id,
        )
        if _handle_identity(temporary) != _handle_identity(
            state.temporary_handle
        ):
            raise OSError("temporary staging root identity changed")
        snapshot = _open_existing_directory(temporary, "snapshot")
        if _handle_identity(snapshot) != _handle_identity(
            state.snapshot_handle
        ):
            raise OSError("staging snapshot identity changed")
    except Exception as exc:
        raise _error(
            "STAGING_DESTINATION_IDENTITY_CHANGED",
            "the destination parent chain no longer identifies staging",
        ) from exc
    finally:
        if snapshot is not None:
            _close_handle(snapshot)
        if temporary is not None:
            _close_handle(temporary)


def _create_destination(state: _StagingState) -> None:
    output_handle = state.root_chain_handles[
        -3
    ]  # output, snapshots, source are the final three handles
    state.staging_id = uuid.uuid4().hex
    state.relative_identifier = (
        f".incremental-staging/{state.staging_id}"
    )
    state.temporary_root = state.staging_root / state.staging_id
    state.snapshot_dir = state.temporary_root / "snapshot"
    if os.name == "nt":
        state.staging_handle = _win_open_child(
            output_handle,
            ".incremental-staging",
            directory=True,
            create=False,
            writable=True,
            open_if=True,
        )
        state.temporary_handle = _win_open_child(
            state.staging_handle,
            state.staging_id,
            directory=True,
            create=True,
            writable=True,
        )
        state.snapshot_handle = _win_open_child(
            state.temporary_handle,
            "snapshot",
            directory=True,
            create=True,
            writable=True,
        )
    else:
        directory_flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            os.mkdir(".incremental-staging", mode=0o700, dir_fd=output_handle)
        except FileExistsError:
            pass
        state.staging_handle = os.open(
            ".incremental-staging",
            directory_flags,
            dir_fd=output_handle,
        )
        os.mkdir(
            state.staging_id,
            mode=0o700,
            dir_fd=state.staging_handle,
        )
        state.temporary_handle = os.open(
            state.staging_id,
            directory_flags,
            dir_fd=state.staging_handle,
        )
        os.mkdir("snapshot", mode=0o700, dir_fd=state.temporary_handle)
        state.snapshot_handle = os.open(
            "snapshot",
            directory_flags,
            dir_fd=state.temporary_handle,
        )
    state.destination_directories = {"": state.snapshot_handle}


def _create_destination_directories(state: _StagingState) -> None:
    source_directories = sorted(
        (entry for entry in state.source_entries if entry.is_dir),
        key=lambda entry: (entry.relative.count("/"), entry.relative),
    )
    for source in source_directories:
        parent = state.destination_directories[source.parent_relative]
        if os.name == "nt":
            handle = _win_open_child(
                parent,
                source.name,
                directory=True,
                create=True,
                writable=True,
            )
            entry = _win_entry(
                handle,
                source.relative,
                source.name,
            )
        else:
            os.mkdir(source.name, mode=0o700, dir_fd=parent)
            flags = (
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_NOFOLLOW", 0)
            )
            handle = os.open(source.name, flags, dir_fd=parent)
            entry = _posix_entry(handle, source.relative, source.name)
        state.destination_directories[source.relative] = handle
        state.destination_entries.append(entry)


def _copy_file_contents(source: _Entry, destination: _Entry) -> None:
    source_digest = hashlib.sha256()
    destination_digest = hashlib.sha256()
    copied = 0
    for chunk in _read_chunks(source.handle):
        source_digest.update(chunk)
        copied += len(chunk)
        if os.name == "nt":
            _win_write(destination.handle, chunk)
        else:
            view = memoryview(chunk)
            while view:
                written = os.write(destination.handle, view)
                if written <= 0:
                    raise OSError("staging write made no progress")
                view = view[written:]
        destination_digest.update(chunk)
    if os.name == "nt":
        if not _kernel32.FlushFileBuffers(destination.handle):
            raise _win_error("FlushFileBuffers failed")
    else:
        os.fsync(destination.handle)
    source.sha256 = source_digest.hexdigest()
    destination.sha256 = destination_digest.hexdigest()
    destination.size = copied
    if copied != source.size:
        raise _error(
            "STAGING_SOURCE_IDENTITY_CHANGED",
            f"source size changed during copy: {source.relative}",
        )


def _copy_files(state: _StagingState) -> None:
    source_files = sorted(
        (entry for entry in state.source_entries if not entry.is_dir),
        key=lambda entry: entry.relative,
    )
    source_identities: set[tuple[object, ...]] = set()
    destination_identities: set[tuple[object, ...]] = set()
    for source in source_files:
        if (
            source.links != 1
            or source.identity in source_identities
        ):
            raise _error(
                "STAGING_FILE_ID_ALIAS_REJECTED",
                f"source file has a hard-link alias: {source.relative}",
            )
        source_identities.add(source.identity)
        parent = state.destination_directories[source.parent_relative]
        if os.name == "nt":
            handle = _win_open_child(
                parent,
                source.name,
                directory=False,
                create=True,
                writable=True,
            )
            destination = _win_entry(
                handle,
                source.relative,
                source.name,
            )
        else:
            flags = (
                os.O_CREAT
                | os.O_EXCL
                | os.O_RDWR
                | getattr(os, "O_NOFOLLOW", 0)
            )
            handle = os.open(
                source.name,
                flags,
                0o600,
                dir_fd=parent,
            )
            destination = _posix_entry(
                handle,
                source.relative,
                source.name,
            )
        state.destination_entries.append(destination)
        if (
            destination.links != 1
            or destination.identity == source.identity
            or destination.identity in destination_identities
        ):
            raise _error(
                "STAGING_FILE_ID_ALIAS_REJECTED",
                f"staged file aliases another file: {source.relative}",
            )
        destination_identities.add(destination.identity)
        _copy_file_contents(source, destination)


def _entry_map(entries: list[_Entry]) -> dict[str, _Entry]:
    return {entry.relative: entry for entry in entries}


def _compare_rescan(
    expected: list[_Entry],
    observed: list[_Entry],
    *,
    source: bool,
) -> None:
    code = (
        "STAGING_SOURCE_IDENTITY_CHANGED"
        if source
        else "STAGING_DESTINATION_IDENTITY_CHANGED"
    )
    expected_map = _entry_map(expected)
    observed_map = _entry_map(observed)
    if set(expected_map) != set(observed_map):
        raise _error(code, "staging tree membership changed")
    for relative, before in expected_map.items():
        after = observed_map[relative]
        if (
            before.is_dir != after.is_dir
            or before.identity != after.identity
            or before.links != after.links
            or (not before.is_dir and before.size != after.size)
        ):
            raise _error(code, f"staging identity changed: {relative}")
        if not after.is_dir:
            after.sha256 = _hash_handle(after.handle)
            if before.sha256 and before.sha256 != after.sha256:
                raise _error(code, f"staging content changed: {relative}")


def _sqlite_handle_uri(entry: _Entry, absolute_path: Path) -> str:
    if os.name == "nt":
        return f"file:{absolute_path.as_posix()}?mode=ro&immutable=1"
    proc_path = Path(f"/proc/self/fd/{entry.handle}")
    if not proc_path.exists():
        raise _error(
            "REPARSE_SAFE_STAGING_UNAVAILABLE",
            "POSIX SQLite validation requires /proc/self/fd",
        )
    return f"file:{proc_path.as_posix()}?mode=ro&immutable=1"


def _validate_sqlite_files(
    entries: list[_Entry],
    *,
    root: Path,
) -> list[sqlite3.Connection]:
    files = {
        entry.relative: entry
        for entry in entries
        if not entry.is_dir
    }
    if any(_SQLITE_SIDECAR.fullmatch(name) for name in files):
        raise _error(
            "STAGING_SQLITE_VALIDATION_FAILED",
            "snapshot contains a SQLite WAL/SHM sidecar",
        )
    connections: list[sqlite3.Connection] = []
    try:
        for relative, entry in sorted(files.items()):
            if not relative.casefold().endswith(".sqlite"):
                continue
            try:
                connection = sqlite3.connect(
                    _sqlite_handle_uri(entry, root / relative),
                    uri=True,
                )
                quick = connection.execute(
                    "PRAGMA quick_check"
                ).fetchone()
                foreign_keys = list(
                    connection.execute("PRAGMA foreign_key_check")
                )
                journal_mode = str(
                    connection.execute("PRAGMA journal_mode").fetchone()[0]
                ).lower()
            except (OSError, sqlite3.DatabaseError, IndexError) as exc:
                raise _error(
                    "STAGING_SQLITE_VALIDATION_FAILED",
                    f"SQLite validation failed: {relative}",
                ) from exc
            if quick != ("ok",) or foreign_keys or journal_mode != "delete":
                connection.close()
                raise _error(
                    "STAGING_SQLITE_VALIDATION_FAILED",
                    f"SQLite is not sealed and valid: {relative}",
                )
            connections.append(connection)
        return connections
    except Exception:
        for connection in connections:
            connection.close()
        raise


def _validate_declared_content(
    entries: list[_Entry],
    *,
    declared: Mapping[str, tuple[int, str]],
    manifest_bytes: bytes,
) -> None:
    files = {
        entry.relative: entry
        for entry in entries
        if not entry.is_dir
    }
    if not set(declared) <= set(files):
        raise _error(
            "STAGING_BASELINE_CHANGED",
            "manifest-declared authority artifacts are missing",
        )
    manifest = files["manifest.json"]
    if manifest.sha256 != hashlib.sha256(manifest_bytes).hexdigest():
        raise _error(
            "STAGING_BASELINE_CHANGED",
            "manifest raw SHA changed during staging",
        )
    for relative, (size, digest) in declared.items():
        entry = files[relative]
        if entry.sha256 != digest or (size >= 0 and entry.size != size):
            raise _error(
                "STAGING_BASELINE_CHANGED",
                f"manifest-declared artifact changed: {relative}",
            )


def _tree_digest(entries: list[_Entry]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda value: value.relative):
        if entry.is_dir:
            continue
        digest.update(entry.relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry.size).encode("ascii"))
        digest.update(b"\0")
        digest.update(entry.sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _authority_digest(
    entries: list[_Entry],
    authority: set[str],
) -> str:
    return _tree_digest(
        [entry for entry in entries if entry.relative in authority]
    )


def _invoke_fault(
    fault_injector: Callable[[str, str], None] | None,
    phase: str,
    relative: str = "",
) -> None:
    if fault_injector is not None:
        fault_injector(phase, relative)


def _close_state_handles(state: _StagingState) -> None:
    for connection in state.sqlite_connections:
        try:
            connection.close()
        except sqlite3.DatabaseError:
            pass
    state.sqlite_connections.clear()
    all_entries = [*state.source_entries, *state.destination_entries]
    closed: set[int] = set()
    for entry in reversed(all_entries):
        if entry.handle < 0 or entry.handle in closed:
            continue
        try:
            _close_handle(entry.handle)
        except OSError:
            pass
        closed.add(entry.handle)
        entry.handle = -1
    for handle in (
        state.snapshot_handle,
        state.temporary_handle,
        state.staging_handle,
        *reversed(state.root_chain_handles),
    ):
        if handle is not None and handle >= 0 and handle not in closed:
            try:
                _close_handle(handle)
            except OSError:
                pass
            closed.add(handle)


def _cleanup_staging(state: _StagingState) -> None:
    """Delete only the unique directory created by this process."""

    if not state.staging_id or state.temporary_handle is None:
        return
    if os.name == "nt":
        seen: set[int] = set()
        for entry in sorted(
            state.destination_entries,
            key=lambda value: (
                value.relative.count("/"),
                not value.is_dir,
            ),
            reverse=True,
        ):
            if entry.handle < 0 or entry.handle in seen:
                continue
            _win_mark_delete(entry.handle)
            _win_close(entry.handle)
            seen.add(entry.handle)
            entry.handle = -1
        if state.snapshot_handle is not None:
            _win_mark_delete(state.snapshot_handle)
            _win_close(state.snapshot_handle)
            state.snapshot_handle = None
        _win_mark_delete(state.temporary_handle)
        _win_close(state.temporary_handle)
        state.temporary_handle = None
        return

    def remove_children(directory_fd: int) -> None:
        for name in sorted(os.listdir(directory_fd)):
            before = os.stat(
                name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(before.st_mode):
                os.unlink(name, dir_fd=directory_fd)
            elif stat.S_ISDIR(before.st_mode):
                flags = (
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                child = os.open(name, flags, dir_fd=directory_fd)
                try:
                    remove_children(child)
                finally:
                    os.close(child)
                os.rmdir(name, dir_fd=directory_fd)
            elif stat.S_ISREG(before.st_mode):
                os.unlink(name, dir_fd=directory_fd)
            else:
                raise OSError("cleanup encountered an unknown special file")

    temporary_identity = _handle_identity(state.temporary_handle)
    matching_names: list[str] = []
    if state.staging_handle is None:
        raise OSError("staging parent handle is unavailable")
    for name in os.listdir(state.staging_handle):
        before = os.stat(
            name,
            dir_fd=state.staging_handle,
            follow_symlinks=False,
        )
        if stat.S_ISDIR(before.st_mode) and (
            int(before.st_dev),
            int(before.st_ino),
        ) == temporary_identity:
            matching_names.append(name)
    if len(matching_names) != 1:
        raise OSError("staging cleanup could not resolve its directory")
    remove_children(state.temporary_handle)
    os.close(state.temporary_handle)
    state.temporary_handle = None
    os.rmdir(matching_names[0], dir_fd=state.staging_handle)


def _baseline_identity_sha256(baseline: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            baseline.payload(),
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _quarantine_relative_parts(value: str) -> tuple[str, ...]:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise _error(
            "ADDITIVE_QUARANTINE_ARTIFACT_SET_MISMATCH",
            "the candidate Evidence directory is not a controlled path",
        )
    return path.parts


def _directory_names(handle: int) -> list[str]:
    if os.name == "nt":
        return _win_names(handle)
    names = os.listdir(handle)
    if len(names) != len(set(names)):
        raise OSError("directory enumeration returned duplicate names")
    return sorted(names)


def _open_directory_entry(
    parent: int,
    name: str,
    *,
    relative: str,
    writable: bool = False,
    create: bool = False,
    share_delete: bool = False,
) -> _Entry:
    if os.name == "nt":
        handle = _win_open_child(
            parent,
            name,
            directory=True,
            create=create,
            writable=writable,
            share_delete=share_delete,
        )
        return _win_entry(handle, relative, name)
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0)
    )
    if create:
        os.mkdir(name, mode=0o700, dir_fd=parent)
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if stat.S_ISLNK(before.st_mode):
        raise _error(
            "ADDITIVE_QUARANTINE_REPARSE_POINT_REJECTED",
            "a candidate Evidence directory is a symlink",
        )
    handle = os.open(name, flags, dir_fd=parent)
    entry = _posix_entry(handle, relative, name)
    if entry.identity != (int(before.st_dev), int(before.st_ino)):
        _close_handle(handle)
        raise _error(
            "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED",
            "a candidate Evidence directory changed before open",
        )
    return entry


def _open_file_entry(
    parent: int,
    name: str,
    *,
    relative: str,
    writable: bool = False,
    create: bool = False,
) -> _Entry:
    if os.name == "nt":
        handle = _win_open_child(
            parent,
            name,
            directory=False,
            create=create,
            writable=writable,
            share_delete=False,
        )
        return _win_entry(handle, relative, name)
    flags = (
        (os.O_CREAT | os.O_EXCL | os.O_RDWR)
        if create
        else os.O_RDONLY
    )
    flags |= getattr(os, "O_NOFOLLOW", 0)
    before = (
        None
        if create
        else os.stat(name, dir_fd=parent, follow_symlinks=False)
    )
    if before is not None and stat.S_ISLNK(before.st_mode):
        raise _error(
            "ADDITIVE_QUARANTINE_REPARSE_POINT_REJECTED",
            "a candidate Evidence artifact is a symlink",
        )
    if before is not None and not stat.S_ISREG(before.st_mode):
        raise _error(
            "ADDITIVE_QUARANTINE_SPECIAL_FILE_REJECTED",
            "a candidate Evidence artifact is not a regular file",
        )
    handle = os.open(name, flags, 0o600, dir_fd=parent)
    entry = _posix_entry(handle, relative, name)
    if before is not None and entry.identity != (
        int(before.st_dev),
        int(before.st_ino),
    ):
        _close_handle(handle)
        raise _error(
            "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED",
            "a candidate Evidence artifact changed before open",
        )
    return entry


def _validate_named_directory(
    parent: int,
    name: str,
    expected: int,
    *,
    source: bool,
) -> None:
    observed: _Entry | None = None
    try:
        observed = _open_directory_entry(
            parent,
            name,
            relative=name,
            share_delete=True,
        )
        if observed.identity != _handle_identity(expected):
            raise OSError("directory identity changed")
    except SafeStagingError:
        raise
    except Exception as exc:
        code = (
            "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED"
            if source
            else "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED"
        )
        raise _error(code, "a pinned directory name changed identity") from exc
    finally:
        if observed is not None:
            _close_handle(observed.handle)


def _validate_absolute_directory_chain(
    path: Path,
    handles: Sequence[int],
    *,
    source: bool,
) -> None:
    absolute = Path(os.path.abspath(path))
    names = absolute.parts[1:]
    if len(handles) != len(names) + 1:
        code = (
            "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED"
            if source
            else "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED"
        )
        raise _error(code, "a pinned absolute directory chain is invalid")
    for index, name in enumerate(names):
        _validate_named_directory(
            handles[index],
            name,
            handles[index + 1],
            source=source,
        )


def _validate_relative_directory_chain(
    handles: Sequence[int],
    *,
    absolute_length: int,
    names: Sequence[str],
    source: bool,
) -> None:
    if len(handles) != absolute_length + len(names):
        code = (
            "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED"
            if source
            else "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED"
        )
        raise _error(code, "a pinned relative directory chain is invalid")
    for index, name in enumerate(names):
        parent_index = absolute_length - 1 + index
        _validate_named_directory(
            handles[parent_index],
            name,
            handles[parent_index + 1],
            source=source,
        )


def _close_unique_handles(
    entries: Sequence[_Entry],
    chains: Sequence[Sequence[int]],
) -> None:
    closed: set[int] = set()
    for entry in reversed(entries):
        if entry.handle < 0 or entry.handle in closed:
            continue
        try:
            _close_handle(entry.handle)
        except OSError:
            pass
        closed.add(entry.handle)
        entry.handle = -1
    for chain in chains:
        for handle in reversed(chain):
            if handle < 0 or handle in closed:
                continue
            try:
                _close_handle(handle)
            except OSError:
                pass
            closed.add(handle)


def _quarantine_error(error: Exception) -> SafeStagingError:
    if isinstance(error, SafeStagingError):
        translations = {
            "STAGING_REPARSE_POINT_REJECTED": (
                "ADDITIVE_QUARANTINE_REPARSE_POINT_REJECTED"
            ),
            "STAGING_SPECIAL_FILE_REJECTED": (
                "ADDITIVE_QUARANTINE_SPECIAL_FILE_REJECTED"
            ),
            "STAGING_FILE_ID_ALIAS_REJECTED": (
                "ADDITIVE_QUARANTINE_HARDLINK_ALIAS_REJECTED"
            ),
            "STAGING_SOURCE_IDENTITY_CHANGED": (
                "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED"
            ),
            "STAGING_DESTINATION_IDENTITY_CHANGED": (
                "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED"
            ),
        }
        code = translations.get(error.gap_code, error.gap_code)
        if code == error.gap_code:
            return error
        return _error(code, str(error))
    return _error(
        "REPARSE_SAFE_ADDITIVE_QUARANTINE_UNAVAILABLE",
        "the candidate Evidence bundle could not be frozen safely",
    )


def _opaque_identity_sha256(domain: bytes, value: object) -> str:
    def normalize(child: object) -> object:
        if isinstance(child, bytes):
            return {"bytesHex": child.hex()}
        if isinstance(child, tuple | list):
            return [normalize(item) for item in child]
        if isinstance(child, dict):
            return {
                str(key): normalize(item)
                for key, item in child.items()
            }
        if child is None or isinstance(child, str | bool | int):
            return child
        raise TypeError("filesystem identity is not canonical")

    encoded = json.dumps(
        normalize(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(domain + b"\0" + encoded).hexdigest()


def inspect_bound_regular_file(
    *,
    root: Path,
    relative_path: str,
) -> SafeBoundFileObservation:
    """Observe one private file through pinned no-follow parent handles."""

    if not isinstance(root, Path) or type(relative_path) is not str:
        raise TypeError("bound file root and relative path are required")
    relative = PurePosixPath(relative_path)
    if (
        not relative_path
        or "\\" in relative_path
        or relative.is_absolute()
        or any(
            part in {"", ".", ".."} or ":" in part
            for part in relative.parts
        )
    ):
        raise _error(
            "BOUND_FILE_PATH_INVALID",
            "the bound file relative path is invalid",
        )
    absolute_root = Path(os.path.abspath(root))
    if root != absolute_root:
        raise _error(
            "BOUND_FILE_ROOT_INVALID",
            "the bound file root must be an absolute normalized path",
        )

    chain: list[int] = []
    directories: list[_Entry] = []
    file_entry: _Entry | None = None
    observed_entry: _Entry | None = None
    try:
        chain = (
            _win_open_absolute_chain(root)
            if os.name == "nt"
            else _posix_open_absolute_chain(root)
        )
        current = chain[-1]
        for index, name in enumerate(relative.parts[:-1]):
            directory = _open_directory_entry(
                current,
                name,
                relative="/".join(relative.parts[: index + 1]),
                share_delete=True,
            )
            directories.append(directory)
            current = directory.handle
        file_entry = _open_file_entry(
            current,
            relative.name,
            relative=relative_path,
        )
        if (
            file_entry.is_dir
            or file_entry.links != 1
            or file_entry.size < 1
        ):
            raise _error(
                "BOUND_FILE_IDENTITY_INVALID",
                "the bound file is not a private non-empty regular file",
            )
        raw_sha256 = _hash_handle(file_entry.handle)
        observed_entry = _open_file_entry(
            current,
            relative.name,
            relative=relative_path,
        )
        if (
            observed_entry.identity != file_entry.identity
            or observed_entry.size != file_entry.size
            or observed_entry.links != file_entry.links
            or observed_entry.change_marker != file_entry.change_marker
            or _hash_handle(observed_entry.handle) != raw_sha256
        ):
            raise _error(
                "BOUND_FILE_IDENTITY_CHANGED",
                "the bound file changed during observation",
            )
        _validate_absolute_directory_chain(
            root,
            chain,
            source=False,
        )
        for index, directory in enumerate(directories):
            parent = chain[-1] if index == 0 else directories[index - 1].handle
            _validate_named_directory(
                parent,
                directory.name,
                directory.handle,
                source=False,
            )
        file_volume = _volume_identity(file_entry.handle)
        root_volume = _volume_identity(chain[-1])
        if file_volume != root_volume:
            raise _error(
                "BOUND_FILE_VOLUME_CHANGED",
                "the bound file is not on its pinned root volume",
            )
        return SafeBoundFileObservation(
            relative_path=relative_path,
            raw_sha256=raw_sha256,
            size_bytes=file_entry.size,
            file_identity_sha256=_opaque_identity_sha256(
                b"ark-kb-bound-file-identity/v1",
                {
                    "platform": os.name,
                    "identity": list(file_entry.identity),
                },
            ),
            volume_identity_sha256=_opaque_identity_sha256(
                b"ark-kb-bound-volume-identity/v1",
                {
                    "platform": os.name,
                    "identity": file_volume,
                },
            ),
        )
    except SafeStagingError:
        raise
    except Exception as exc:
        raise _error(
            "BOUND_FILE_OBSERVATION_FAILED",
            "the bound file could not be observed safely",
        ) from exc
    finally:
        _close_unique_handles(
            [
                *directories,
                *([file_entry] if file_entry is not None else []),
                *([observed_entry] if observed_entry is not None else []),
            ],
            [chain],
        )


def freeze_blueprint_evidence_bundle(
    *,
    source_root: Path,
    source_relative_directory: str,
    temporary_root: Path,
    staging_id: str,
    staging_identity: tuple[object, ...],
    source_id: str,
    fault_injector: Callable[[str, str], None] | None = None,
) -> SafeFrozenBlueprintBundle:
    """Copy one exact Evidence SQLite bundle through pinned no-follow handles."""

    if (
        not isinstance(source_root, Path)
        or not isinstance(temporary_root, Path)
        or not re.fullmatch(r"[0-9a-f]{32}", staging_id)
        or not re.fullmatch(r"[0-9a-f]{64}", source_id)
        or not isinstance(staging_identity, tuple)
        or not staging_identity
        or (
            fault_injector is not None
            and not callable(fault_injector)
        )
    ):
        raise TypeError("safe additive quarantine identity is invalid")
    source_parts = _quarantine_relative_parts(
        source_relative_directory
    )
    expected_temporary = (
        temporary_root.parent / staging_id
    )
    if (
        temporary_root != expected_temporary
        or temporary_root.parent.name != ".incremental-staging"
    ):
        raise _error(
            "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED",
            "quarantine must use the returned staging temporary root",
        )

    source_chain: list[int] = []
    destination_chain: list[int] = []
    source_directory_entries: list[_Entry] = []
    source_entries: list[_Entry] = []
    destination_entries: list[_Entry] = []
    quarantine_entry: _Entry | None = None
    bundle_entry: _Entry | None = None
    cleanup_state: _StagingState | None = None
    residual = (
        f".incremental-staging/{staging_id}/quarantine"
    )
    original_error: SafeStagingError | None = None
    source_absolute_length = 0
    try:
        source_chain = (
            _win_open_absolute_chain(source_root)
            if os.name == "nt"
            else _posix_open_absolute_chain(source_root)
        )
        source_absolute_length = len(source_chain)
        source_root_handle = source_chain[-1]
        source_parent = source_root_handle
        relative = ""
        for part in source_parts:
            relative = f"{relative}/{part}" if relative else part
            entry = _open_directory_entry(
                source_parent,
                part,
                relative=relative,
            )
            source_directory_entries.append(entry)
            source_chain.append(entry.handle)
            source_parent = entry.handle
        source_bundle_handle = source_chain[-1]
        names = _directory_names(source_bundle_handle)
        if "evidence.sqlite" not in names:
            raise _error(
                "ADDITIVE_QUARANTINE_ARTIFACT_SET_MISMATCH",
                "the candidate Evidence SQLite is missing",
            )
        artifact_names = ["evidence.sqlite"]
        if "manifest.json" in names:
            artifact_names.append("manifest.json")
        for name in artifact_names:
            entry = _open_file_entry(
                source_bundle_handle,
                name,
                relative=name,
            )
            if entry.links != 1:
                raise _error(
                    "ADDITIVE_QUARANTINE_HARDLINK_ALIAS_REJECTED",
                    "a candidate Evidence artifact has a hard-link alias",
                )
            source_entries.append(entry)
        if len({entry.identity for entry in source_entries}) != len(
            source_entries
        ):
            raise _error(
                "ADDITIVE_QUARANTINE_HARDLINK_ALIAS_REJECTED",
                "candidate Evidence artifacts alias each other",
            )
        try:
            _invoke_fault(
                fault_injector,
                "after_source_enumeration",
                source_relative_directory,
            )
        except OSError as exc:
            raise _error(
                "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED",
                "the candidate Evidence directory could not remain pinned",
            ) from exc
        _validate_absolute_directory_chain(
            source_root,
            source_chain[:source_absolute_length],
            source=True,
        )
        _validate_relative_directory_chain(
            source_chain,
            absolute_length=source_absolute_length,
            names=source_parts,
            source=True,
        )

        destination_chain = (
            _win_open_absolute_chain(temporary_root)
            if os.name == "nt"
            else _posix_open_absolute_chain(temporary_root)
        )
        temporary_handle = destination_chain[-1]
        if _handle_identity(temporary_handle) != staging_identity:
            raise _error(
                "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED",
                "the staging temporary root identity changed",
            )
        _validate_absolute_directory_chain(
            temporary_root,
            destination_chain,
            source=False,
        )
        quarantine_entry = _open_directory_entry(
            temporary_handle,
            "quarantine",
            relative="quarantine",
            writable=True,
            create=True,
        )
        bundle_entry = _open_directory_entry(
            quarantine_entry.handle,
            source_id,
            relative=source_id,
            writable=True,
            create=True,
        )
        cleanup_state = _StagingState(
            snapshot_root=temporary_root.parent.parent,
            staging_root=temporary_root,
            staging_id="quarantine",
            relative_identifier=residual,
            temporary_handle=quarantine_entry.handle,
            staging_handle=temporary_handle,
            destination_entries=[bundle_entry],
        )
        if _volume_identity(temporary_handle) != _volume_identity(
            quarantine_entry.handle
        ):
            raise _error(
                "ADDITIVE_QUARANTINE_NOT_ON_TARGET_VOLUME",
                "quarantine is not on the staged snapshot volume",
            )
        try:
            _invoke_fault(
                fault_injector,
                "after_destination_created",
                residual,
            )
        except OSError as exc:
            raise _error(
                "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED",
                "the quarantine parent could not remain pinned",
            ) from exc
        _validate_named_directory(
            temporary_handle,
            "quarantine",
            quarantine_entry.handle,
            source=False,
        )
        _validate_named_directory(
            quarantine_entry.handle,
            source_id,
            bundle_entry.handle,
            source=False,
        )
        _invoke_fault(fault_injector, "before_copy")

        source_identities = {entry.identity for entry in source_entries}
        destination_identities: set[tuple[object, ...]] = set()
        for source in source_entries:
            destination = _open_file_entry(
                bundle_entry.handle,
                source.name,
                relative=source.name,
                writable=True,
                create=True,
            )
            destination_entries.append(destination)
            cleanup_state.destination_entries.append(destination)
            if (
                destination.links != 1
                or destination.identity in source_identities
                or destination.identity in destination_identities
            ):
                raise _error(
                    "ADDITIVE_QUARANTINE_HARDLINK_ALIAS_REJECTED",
                    "a quarantine artifact aliases another file",
                )
            destination_identities.add(destination.identity)
            try:
                _copy_file_contents(source, destination)
            except SafeStagingError as exc:
                raise _quarantine_error(exc) from exc
        try:
            _invoke_fault(fault_injector, "after_copy")
        except OSError as exc:
            raise _error(
                "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED",
                "a candidate Evidence artifact could not remain stable",
            ) from exc
        try:
            _invoke_fault(fault_injector, "before_receipt")
        except OSError as exc:
            raise _error(
                "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED",
                "the live candidate Evidence changed after quarantine",
            ) from exc

        _validate_absolute_directory_chain(
            source_root,
            source_chain[:source_absolute_length],
            source=True,
        )
        _validate_relative_directory_chain(
            source_chain,
            absolute_length=source_absolute_length,
            names=source_parts,
            source=True,
        )
        observed_names = _directory_names(source_bundle_handle)
        if (
            ("manifest.json" in observed_names)
            != ("manifest.json" in artifact_names)
            or "evidence.sqlite" not in observed_names
        ):
            raise _error(
                "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED",
                "the candidate Evidence artifact set changed",
            )
        observed_source = [
            _open_file_entry(
                source_bundle_handle,
                source.name,
                relative=source.name,
            )
            for source in source_entries
        ]
        try:
            _compare_rescan(
                source_entries,
                observed_source,
                source=True,
            )
        except SafeStagingError as exc:
            raise _quarantine_error(exc) from exc
        finally:
            _close_entries(observed_source)

        _validate_named_directory(
            temporary_handle,
            "quarantine",
            quarantine_entry.handle,
            source=False,
        )
        _validate_named_directory(
            quarantine_entry.handle,
            source_id,
            bundle_entry.handle,
            source=False,
        )
        observed_destination, observed_directories = _walk(
            bundle_entry.handle,
            share_delete=True,
        )
        preserve_observed_for_cleanup = False
        try:
            observed_paths = {
                entry.relative for entry in observed_destination
            }
            if (
                any(entry.is_dir for entry in observed_destination)
                or observed_paths != set(artifact_names)
            ):
                if os.name == "nt":
                    # Reopen the complete destination tree with DELETE access
                    # only after the exact-set check fails.  Keeping the normal
                    # rescan read-only avoids a sharing violation with the
                    # pinned writable artifact handles.
                    _close_file_entries(destination_entries)
                    _close_entries(
                        observed_destination,
                        keep_directories={bundle_entry.handle},
                    )
                    observed_destination, observed_directories = _walk(
                        bundle_entry.handle,
                        writable=True,
                        share_delete=True,
                    )
                    cleanup_state.destination_entries = [
                        bundle_entry,
                        *observed_destination,
                    ]
                    preserve_observed_for_cleanup = True
                raise _error(
                    "ADDITIVE_QUARANTINE_ARTIFACT_SET_MISMATCH",
                    "quarantine contains an unexpected artifact",
                )
            _compare_rescan(
                destination_entries,
                observed_destination,
                source=False,
            )
        except SafeStagingError as exc:
            raise _quarantine_error(exc) from exc
        finally:
            if not preserve_observed_for_cleanup:
                _close_entries(
                    observed_destination,
                    keep_directories={bundle_entry.handle},
                )
            del observed_directories

        _validate_absolute_directory_chain(
            source_root,
            source_chain[:source_absolute_length],
            source=True,
        )
        _validate_relative_directory_chain(
            source_chain,
            absolute_length=source_absolute_length,
            names=source_parts,
            source=True,
        )
        _validate_absolute_directory_chain(
            temporary_root,
            destination_chain,
            source=False,
        )
        _validate_named_directory(
            temporary_handle,
            "quarantine",
            quarantine_entry.handle,
            source=False,
        )
        _validate_named_directory(
            quarantine_entry.handle,
            source_id,
            bundle_entry.handle,
            source=False,
        )
        tree_digest = _tree_digest(destination_entries)
        result = SafeFrozenBlueprintBundle(
            staging_id=staging_id,
            source_id=source_id,
            quarantine_root=temporary_root / "quarantine",
            bundle_root=temporary_root / "quarantine" / source_id,
            artifacts=tuple(
                SafeCopiedArtifact(
                    relative=entry.relative,
                    sha256=entry.sha256,
                    size_bytes=entry.size,
                    file_identity_sha256=_opaque_identity_sha256(
                        b"ark-kb-bound-file-identity/v1",
                        {
                            "platform": os.name,
                            "identity": list(entry.identity),
                        },
                    ),
                )
                for entry in sorted(
                    destination_entries,
                    key=lambda value: value.relative,
                )
            ),
            quarantine_tree_digest=tree_digest,
            quarantine_identity=_handle_identity(
                quarantine_entry.handle
            ),
        )
        _close_unique_handles(
            [
                *source_entries,
                *destination_entries,
                *source_directory_entries,
                bundle_entry,
                quarantine_entry,
            ],
            [source_chain, destination_chain],
        )
        return result
    except Exception as exc:
        original_error = _quarantine_error(exc)
        if original_error is not exc:
            original_error.__cause__ = exc

    try:
        if cleanup_state is not None:
            _cleanup_staging(cleanup_state)
    except Exception as cleanup_error:
        _close_unique_handles(
            [
                *source_entries,
                *destination_entries,
                *source_directory_entries,
                *(
                    [bundle_entry]
                    if bundle_entry is not None
                    else []
                ),
                *(
                    [quarantine_entry]
                    if quarantine_entry is not None
                    else []
                ),
            ],
            [source_chain, destination_chain],
        )
        raise SafeStagingError(
            "ADDITIVE_QUARANTINE_CLEANUP_UNCERTAIN",
            "quarantine cleanup could not prove removal of its directory",
            status="UNCERTAIN",
            residual_identifier=residual,
        ) from cleanup_error
    _close_unique_handles(
        [
            *source_entries,
            *destination_entries,
            *source_directory_entries,
            *(
                [bundle_entry]
                if bundle_entry is not None
                else []
            ),
            *(
                [quarantine_entry]
                if quarantine_entry is not None
                else []
            ),
        ],
        [source_chain, destination_chain],
    )
    assert original_error is not None
    raise original_error


def validate_frozen_blueprint_bundle(
    bundle: SafeFrozenBlueprintBundle,
    *,
    temporary_root: Path,
    staging_identity: tuple[object, ...],
) -> SafeValidatedBlueprintBundle:
    """Reopen and verify the exact quarantine before any staged Core write."""

    if (
        type(bundle) is not SafeFrozenBlueprintBundle
        or not isinstance(temporary_root, Path)
        or not isinstance(staging_identity, tuple)
        or not staging_identity
        or bundle.quarantine_root
        != temporary_root / "quarantine"
        or bundle.bundle_root
        != temporary_root / "quarantine" / bundle.source_id
    ):
        raise _error(
            "ADDITIVE_QUARANTINE_RECEIPT_INVALID",
            "the frozen quarantine identity is invalid",
        )
    chain: list[int] = []
    entries: list[_Entry] = []
    quarantine: _Entry | None = None
    source_directory: _Entry | None = None
    try:
        chain = (
            _win_open_absolute_chain(temporary_root)
            if os.name == "nt"
            else _posix_open_absolute_chain(temporary_root)
        )
        temporary_handle = chain[-1]
        if _handle_identity(temporary_handle) != staging_identity:
            raise _error(
                "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED",
                "the staging temporary root identity changed",
            )
        quarantine = _open_directory_entry(
            temporary_handle,
            "quarantine",
            relative="quarantine",
        )
        if quarantine.identity != bundle.quarantine_identity:
            raise _error(
                "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED",
                "the quarantine directory identity changed",
            )
        source_directory = _open_directory_entry(
            quarantine.handle,
            bundle.source_id,
            relative=bundle.source_id,
        )
        entries, directories = _walk(
            source_directory.handle,
        )
        expected = {
            artifact.relative: artifact
            for artifact in bundle.artifacts
        }
        if (
            any(entry.is_dir for entry in entries)
            or {entry.relative for entry in entries} != set(expected)
        ):
            raise _error(
                "ADDITIVE_QUARANTINE_ARTIFACT_SET_MISMATCH",
                "quarantine artifact membership changed",
            )
        identities: set[tuple[object, ...]] = set()
        contents: list[tuple[str, bytes]] = []
        for entry in entries:
            if entry.links != 1 or entry.identity in identities:
                raise _error(
                    "ADDITIVE_QUARANTINE_HARDLINK_ALIAS_REJECTED",
                    "a quarantine artifact has a hard-link alias",
                )
            identities.add(entry.identity)
            content = b"".join(_read_chunks(entry.handle))
            entry.sha256 = hashlib.sha256(content).hexdigest()
            artifact = expected[entry.relative]
            if (
                entry.size != artifact.size_bytes
                or entry.sha256 != artifact.sha256
                or _opaque_identity_sha256(
                    b"ark-kb-bound-file-identity/v1",
                    {
                        "platform": os.name,
                        "identity": list(entry.identity),
                    },
                )
                != artifact.file_identity_sha256
            ):
                raise _error(
                    "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED",
                    "a quarantine artifact changed after freezing",
                )
            contents.append((entry.relative, content))
        digest = _tree_digest(entries)
        if digest != bundle.quarantine_tree_digest:
            raise _error(
                "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED",
                "the quarantine tree digest changed",
            )
        _validate_absolute_directory_chain(
            temporary_root,
            chain,
            source=False,
        )
        _validate_named_directory(
            temporary_handle,
            "quarantine",
            quarantine.handle,
            source=False,
        )
        _validate_named_directory(
            quarantine.handle,
            bundle.source_id,
            source_directory.handle,
            source=False,
        )
        del directories
        _close_unique_handles(
            [
                *entries,
                source_directory,
                quarantine,
            ],
            [chain],
        )
        return SafeValidatedBlueprintBundle(
            quarantine_tree_digest=digest,
            artifacts=tuple(sorted(contents)),
        )
    except Exception as exc:
        _close_unique_handles(
            [
                *entries,
                *(
                    [source_directory]
                    if source_directory is not None
                    else []
                ),
                *(
                    [quarantine]
                    if quarantine is not None
                    else []
                ),
            ],
            [chain],
        )
        raise _quarantine_error(exc) from exc


def stage_snapshot_tree(
    baseline: Any,
    *,
    staging_root: Path,
    validate_baseline: Callable[[], None],
    fault_injector: Callable[[str, str], None] | None = None,
) -> SafeStagedSnapshot:
    """Create one independently copied, unpublished staging snapshot."""

    expected_root = baseline.snapshot_root / ".incremental-staging"
    if staging_root != expected_root:
        raise _error(
            "STAGING_DESTINATION_IDENTITY_CHANGED",
            "staging root must be the reserved child of snapshot_root",
        )
    state = _StagingState(
        snapshot_root=baseline.snapshot_root,
        staging_root=staging_root,
    )
    manifest_bytes = baseline.current_snapshot.manifest_bytes
    manifest = _strict_manifest(manifest_bytes)
    declared, authority, cache_disposition = _declared_artifacts(
        manifest,
        expected_build_id=baseline.base_build_id,
        expected_manifest_sha256=baseline.base_manifest_sha256,
    )
    baseline_identity = _baseline_identity_sha256(baseline)
    original_error: SafeStagingError | None = None
    try:
        try:
            validate_baseline()
        except Exception as exc:
            raise _error(
                "STAGING_BASELINE_CHANGED",
                "the UpdateBaseline changed before staging",
            ) from exc
        source_root = _open_source_root(state, baseline.base_build_id)
        state.source_entries, state.source_directories = _walk(source_root)
        if not state.source_entries:
            raise _error(
                "STAGING_SOURCE_IDENTITY_CHANGED",
                "the immutable snapshot tree is empty",
            )
        try:
            _invoke_fault(fault_injector, "after_source_enumeration")
        except Exception as exc:
            raise _error(
                "STAGING_SOURCE_IDENTITY_CHANGED",
                "the source tree changed after handle-based enumeration",
            ) from exc
        _validate_source_root_binding(
            state,
            build_id=baseline.base_build_id,
            source_root=source_root,
        )

        _create_destination(state)
        assert state.temporary_root is not None
        assert state.snapshot_dir is not None
        assert state.temporary_handle is not None
        if _volume_identity(source_root) != _volume_identity(
            state.temporary_handle
        ):
            raise _error(
                "STAGING_NOT_ON_TARGET_VOLUME",
                "staging is not on the immutable snapshot volume",
            )
        try:
            _invoke_fault(
                fault_injector,
                "after_destination_created",
                state.relative_identifier,
            )
        except Exception as exc:
            raise _error(
                "STAGING_DESTINATION_IDENTITY_CHANGED",
                "the staging destination was replaced during creation",
            ) from exc
        _validate_destination_root_binding(state)
        _create_destination_directories(state)
        if os.name == "nt":
            _close_file_entries(state.source_entries)
            state.sqlite_connections.extend(
                _validate_sqlite_files(
                    state.source_entries,
                    root=baseline.current_snapshot.snapshot_dir,
                )
            )
            observed_source, observed_directories = _walk(source_root)
            _compare_rescan(
                state.source_entries,
                observed_source,
                source=True,
            )
            _close_entries(
                state.source_entries,
                keep_directories={source_root},
            )
            state.source_entries = observed_source
            state.source_directories = observed_directories
        else:
            state.sqlite_connections.extend(
                _validate_sqlite_files(
                    state.source_entries,
                    root=baseline.current_snapshot.snapshot_dir,
                )
            )
        _invoke_fault(fault_injector, "before_copy")
        _validate_source_root_binding(
            state,
            build_id=baseline.base_build_id,
            source_root=source_root,
        )
        _validate_destination_root_binding(state)
        _copy_files(state)
        _validate_declared_content(
            state.source_entries,
            declared=declared,
            manifest_bytes=manifest_bytes,
        )
        _validate_declared_content(
            state.destination_entries,
            declared=declared,
            manifest_bytes=manifest_bytes,
        )
        if os.name == "nt":
            _close_file_entries(state.destination_entries)
            destination_connections = _validate_sqlite_files(
                state.destination_entries,
                root=state.snapshot_dir,
            )
            for connection in destination_connections:
                connection.close()
            observed_destination, observed_directories = _walk(
                state.snapshot_handle,
                share_delete=True,
            )
            try:
                _compare_rescan(
                    state.destination_entries,
                    observed_destination,
                    source=False,
                )
            finally:
                _close_entries(
                    observed_destination,
                    keep_directories={state.snapshot_handle},
                )
            previous_files = {
                entry.relative: entry
                for entry in state.destination_entries
                if not entry.is_dir
            }
            replacement_files: list[_Entry] = []
            for relative, previous in sorted(previous_files.items()):
                parent = state.destination_directories[
                    previous.parent_relative
                ]
                handle = _win_open_child(
                    parent,
                    previous.name,
                    directory=False,
                    create=False,
                    writable=True,
                )
                replacement = _win_entry(
                    handle,
                    relative,
                    previous.name,
                )
                replacement.sha256 = previous.sha256
                replacement_files.append(replacement)
            state.destination_entries = [
                entry
                for entry in state.destination_entries
                if entry.is_dir
            ] + replacement_files
            del observed_directories
        else:
            state.sqlite_connections.extend(
                _validate_sqlite_files(
                    state.destination_entries,
                    root=state.snapshot_dir,
                )
            )

        _close_file_entries(state.source_entries)
        _invoke_fault(fault_injector, "after_copy")
        _validate_source_root_binding(
            state,
            build_id=baseline.base_build_id,
            source_root=source_root,
        )
        _validate_destination_root_binding(state)
        try:
            validate_baseline()
        except Exception as exc:
            raise _error(
                "STAGING_BASELINE_CHANGED",
                "pointer, manifest, source, diff, or UpdateBaseline changed",
            ) from exc
        if _baseline_identity_sha256(baseline) != baseline_identity:
            raise _error(
                "STAGING_BASELINE_CHANGED",
                "the UpdateBaseline identity changed during staging",
            )

        observed_source, observed_source_directories = _walk(source_root)
        try:
            _compare_rescan(
                state.source_entries,
                observed_source,
                source=True,
            )
        finally:
            _close_entries(
                observed_source,
                keep_directories={source_root},
            )
            del observed_source_directories

        assert state.snapshot_handle is not None
        observed_destination, observed_destination_directories = _walk(
            state.snapshot_handle,
            share_delete=True,
        )
        try:
            _compare_rescan(
                state.destination_entries,
                observed_destination,
                source=False,
            )
        finally:
            _close_entries(
                observed_destination,
                keep_directories={state.snapshot_handle},
            )
            del observed_destination_directories

        source_tree_digest = _tree_digest(state.source_entries)
        staged_tree_digest = _tree_digest(state.destination_entries)
        if source_tree_digest != staged_tree_digest:
            raise _error(
                "STAGING_DESTINATION_IDENTITY_CHANGED",
                "the staged whole-tree digest differs from the source",
            )
        _invoke_fault(fault_injector, "before_receipt")
        _validate_source_root_binding(
            state,
            build_id=baseline.base_build_id,
            source_root=source_root,
        )
        _validate_destination_root_binding(state)
        try:
            validate_baseline()
        except Exception as exc:
            raise _error(
                "STAGING_BASELINE_CHANGED",
                "the locked baseline changed before the staging receipt",
            ) from exc
        created_at = datetime.now(UTC).isoformat(timespec="seconds")
        files = [
            entry for entry in state.destination_entries if not entry.is_dir
        ]
        core_entry = next(
            entry for entry in files if entry.relative == "core.sqlite"
        )
        body: dict[str, object] = {
            "schema": STAGING_RECEIPT_SCHEMA,
            "evidenceClass": STAGING_EVIDENCE_CLASS,
            "baseBuildId": baseline.base_build_id,
            "pointerSha256": baseline.base_pointer_sha256,
            "manifestSha256": baseline.base_manifest_sha256,
            "baseSourceManifestFingerprint": (
                baseline.base_source_manifest_fingerprint
            ),
            "sourceManifestFingerprint": (
                baseline.candidate_source_manifest_fingerprint
            ),
            "sourceDiffSha256": baseline.source_diff_sha256,
            "updateBaselineIdentitySha256": baseline_identity,
            "sourceTreeDigest": source_tree_digest,
            "stagedTreeDigest": staged_tree_digest,
            "authorityDigest": _authority_digest(
                state.destination_entries,
                authority,
            ),
            "coreFileIdentitySha256": _opaque_identity_sha256(
                b"ark-kb-bound-file-identity/v1",
                {
                    "platform": os.name,
                    "identity": list(core_entry.identity),
                },
            ),
            "sameVolume": True,
            "sourceVerifiedUnchanged": True,
            "reparsePointCount": 0,
            "hardlinkAliasCount": 0,
            "copiedAuthorityFileCount": len(authority),
            "copiedNonAuthorityFileCount": len(files) - len(authority),
            "cacheDisposition": cache_disposition,
            "fileCount": len(files),
            "totalBytes": sum(entry.size for entry in files),
            "createdAt": created_at,
            "stagingRelativePath": (
                f"{state.relative_identifier}/snapshot"
            ),
            "published": False,
            "productionAuthority": False,
            "e4Scenario2Complete": False,
            "cutoverEligible": False,
            "mode": "shadow",
            "defaultQuerySource": "legacy",
        }
        proof = hashlib.sha256(
            json.dumps(
                body,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        result = SafeStagedSnapshot(
            base_build_id=baseline.base_build_id,
            staging_id=state.staging_id,
            temporary_root=state.temporary_root,
            snapshot_dir=state.snapshot_dir,
            manifest_sha256=baseline.base_manifest_sha256,
            copied_files=len(files),
            receipt={**body, "proof": f"staging-proof://{proof}"},
            cleanup_identity=_handle_identity(state.temporary_handle),
        )
        _close_state_handles(state)
        return result
    except SafeStagingError as exc:
        original_error = exc
    except Exception as exc:
        original_error = _error(
            "STAGING_COPY_FAILED",
            "the immutable snapshot could not be copied safely",
        )
        original_error.__cause__ = exc

    try:
        _cleanup_staging(state)
    except Exception as cleanup_error:
        _close_state_handles(state)
        raise SafeStagingError(
            "STAGING_CLEANUP_UNCERTAIN",
            "staging cleanup could not prove removal of its unique directory",
            status="UNCERTAIN",
            residual_identifier=state.relative_identifier,
        ) from cleanup_error
    _close_state_handles(state)
    assert original_error is not None
    raise original_error


def cleanup_staged_snapshot(
    *,
    snapshot_root: Path,
    staging_id: str,
    expected_identity: tuple[object, ...],
) -> None:
    """Remove one returned staging tree by its pinned directory identity."""

    if (
        not isinstance(snapshot_root, Path)
        or not re.fullmatch(r"[0-9a-f]{32}", staging_id)
        or not isinstance(expected_identity, tuple)
        or not expected_identity
    ):
        raise TypeError("safe staging cleanup identity is invalid")
    root = snapshot_root.resolve()
    state = _StagingState(
        snapshot_root=root,
        staging_root=root / ".incremental-staging",
        staging_id=staging_id,
        relative_identifier=f".incremental-staging/{staging_id}",
    )
    try:
        if os.name == "nt":
            state.root_chain_handles = _win_open_absolute_chain(root)
            output_handle = state.root_chain_handles[-1]
            state.staging_handle = _win_open_child(
                output_handle,
                ".incremental-staging",
                directory=True,
                create=False,
                writable=True,
            )
            state.temporary_handle = _win_open_child(
                state.staging_handle,
                staging_id,
                directory=True,
                create=False,
                writable=True,
            )
        else:
            state.root_chain_handles = _posix_open_absolute_chain(root)
            output_handle = state.root_chain_handles[-1]
            directory_flags = (
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_NOFOLLOW", 0)
            )
            state.staging_handle = os.open(
                ".incremental-staging",
                directory_flags,
                dir_fd=output_handle,
            )
            state.temporary_handle = os.open(
                staging_id,
                directory_flags,
                dir_fd=state.staging_handle,
            )
        if _handle_identity(state.temporary_handle) != expected_identity:
            raise OSError("staging cleanup identity changed")
        state.destination_entries, state.destination_directories = _walk(
            state.temporary_handle,
            writable=os.name == "nt",
        )
        _cleanup_staging(state)
    except Exception as exc:
        _close_state_handles(state)
        raise SafeStagingError(
            "STAGING_CLEANUP_UNCERTAIN",
            "staging cleanup could not prove removal of its unique directory",
            status="UNCERTAIN",
            residual_identifier=state.relative_identifier,
        ) from exc
    _close_state_handles(state)


__all__ = [
    "STAGING_EVIDENCE_CLASS",
    "STAGING_RECEIPT_SCHEMA",
    "SafeBoundFileObservation",
    "SafeStagedSnapshot",
    "SafeStagingError",
    "cleanup_staged_snapshot",
    "inspect_bound_regular_file",
    "stage_snapshot_tree",
]
