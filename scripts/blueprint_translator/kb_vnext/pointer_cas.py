"""Cross-process compare-and-swap for the vNext current pointer."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl


CURRENT_POINTER_NAME = "current.json"
CURRENT_POINTER_KEYS = frozenset({"buildId", "snapshotRelativePath"})
CURRENT_POINTER_LOCK_NAME = ".current.json.lock"
POINTER_CAS_RECEIPT_SCHEMA = (
    "ark-kb-current-pointer-cas-receipt/v1"
)
SNAPSHOT_SCHEMA = "ark-kb-vnext-snapshot/v1"
_SAFE_BUILD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_SAFE_OPERATION = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MAX_POINTER_BYTES = 16 * 1024
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_LOCK_RETRY_SECONDS = 0.005
_DEFAULT_LOCK_TIMEOUT_SECONDS = 2.0
_WINDOWS_REPLACE_RETRY_SECONDS = 0.005
_WINDOWS_REPLACE_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True)
class CurrentPointerBaseline:
    """Expected pointer identity, including its exact serialized bytes."""

    build_id: str | None
    pointer_sha256: str | None

    def __post_init__(self) -> None:
        if (self.build_id is None) != (self.pointer_sha256 is None):
            raise ValueError(
                "pointer baseline buildId and SHA-256 must both be "
                "present or both be absent"
            )
        if self.build_id is not None:
            _safe_build_id(self.build_id)
        if self.pointer_sha256 is not None and (
            len(self.pointer_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.pointer_sha256
            )
        ):
            raise ValueError(
                "pointer baseline SHA-256 must be lowercase hexadecimal"
            )


@dataclass(frozen=True)
class CurrentSnapshotBaseline:
    """One exact current pointer and manifest; not a whole-tree attestation."""

    pointer: CurrentPointerBaseline
    snapshot_dir: Path
    manifest_bytes: bytes
    manifest_sha256: str
    tree_validated: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.pointer) is not CurrentPointerBaseline
            or self.pointer.build_id is None
            or self.pointer.pointer_sha256 is None
        ):
            raise ValueError(
                "snapshot baseline requires an existing current pointer"
            )
        if (
            type(self.manifest_bytes) is not bytes
            or not self.manifest_bytes
            or len(self.manifest_bytes) > _MAX_MANIFEST_BYTES
            or hashlib.sha256(self.manifest_bytes).hexdigest()
            != self.manifest_sha256
            or self.tree_validated is not False
        ):
            raise ValueError(
                "snapshot baseline manifest contract is invalid"
            )
        _validate_optional_sha256(
            self.manifest_sha256,
            label="snapshot baseline manifest SHA-256",
        )
        if self.snapshot_dir.name != self.pointer.build_id:
            raise ValueError(
                "snapshot baseline path and buildId differ"
            )


class PointerCASError(RuntimeError):
    """Base class for typed current-pointer write failures."""


class PointerCASConflictError(PointerCASError, ValueError):
    """The locked pointer no longer matches the expected raw baseline."""


class PointerCASDestinationError(PointerCASError, ValueError):
    """The requested immutable destination is missing or invalid."""


class PointerCASLockTimeoutError(PointerCASError, TimeoutError):
    """The shared pointer lock could not be acquired within its bound."""


class PointerCASWriteError(PointerCASError):
    """A pointer write failed before atomic replacement was attempted."""

    def __init__(
        self,
        message: str,
        *,
        receipt: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.receipt = receipt


class PointerCASUncertainStateError(PointerCASError):
    """Replacement was attempted, so callers must not claim rollback."""

    def __init__(
        self,
        message: str,
        *,
        receipt: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.receipt = receipt


class _ReplaceNotPerformedError(OSError):
    """A sharing violation ended with the source rename still present."""


def _safe_build_id(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not _SAFE_BUILD_ID.fullmatch(value)
        or value in {".", ".."}
    ):
        raise ValueError("snapshot buildId is missing or unsafe")
    return value


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _strict_json_object(
    raw: bytes,
    *,
    label: str,
    maximum_bytes: int,
) -> dict[str, object]:
    if not raw or len(raw) > maximum_bytes:
        raise ValueError(f"{label} size is invalid")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _parse_pointer_bytes(raw: bytes) -> str:
    pointer = _strict_json_object(
        raw,
        label="current snapshot pointer",
        maximum_bytes=_MAX_POINTER_BYTES,
    )
    if set(pointer) != CURRENT_POINTER_KEYS:
        raise ValueError(
            "current snapshot pointer must contain only buildId and "
            "snapshotRelativePath"
        )
    build_id = _safe_build_id(pointer.get("buildId"))
    if pointer.get("snapshotRelativePath") != f"snapshots/{build_id}":
        raise ValueError(
            "snapshotRelativePath must equal snapshots/<buildId>"
        )
    return build_id


def _read_bounded_file(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
) -> bytes:
    try:
        before = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if (
        _is_link_junction_or_reparse(path)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
    ):
        raise ValueError(f"{label} is not a private regular file")
    try:
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino)
                != (before.st_dev, before.st_ino)
                or opened.st_size != before.st_size
                or opened.st_mtime_ns != before.st_mtime_ns
            ):
                raise ValueError(f"{label} changed before open")
            raw = handle.read(maximum_bytes + 1)
            opened_after = os.fstat(handle.fileno())
    except OSError as exc:
        raise ValueError(f"{label} is unreadable") from exc
    try:
        after = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"{label} changed during read") from exc
    if (
        _is_link_junction_or_reparse(path)
        or not stat.S_ISREG(after.st_mode)
        or after.st_nlink != 1
        or (opened_after.st_dev, opened_after.st_ino)
        != (opened.st_dev, opened.st_ino)
        or opened_after.st_size != opened.st_size
        or opened_after.st_mtime_ns != opened.st_mtime_ns
        or (after.st_dev, after.st_ino)
        != (opened.st_dev, opened.st_ino)
        or after.st_size != opened.st_size
        or after.st_mtime_ns != opened.st_mtime_ns
    ):
        raise ValueError(f"{label} changed during read")
    if not raw or len(raw) > maximum_bytes:
        raise ValueError(f"{label} size is invalid")
    return raw


def _read_pointer_bytes(snapshot_root: Path) -> bytes | None:
    pointer_path = snapshot_root / CURRENT_POINTER_NAME
    if _is_link_junction_or_reparse(pointer_path):
        raise ValueError("current snapshot pointer is not a regular file")
    if not pointer_path.exists():
        return None
    if not pointer_path.is_file():
        raise ValueError("current snapshot pointer is not a regular file")
    try:
        return _read_bounded_file(
            pointer_path,
            maximum_bytes=_MAX_POINTER_BYTES,
            label="current snapshot pointer",
        )
    except OSError as exc:
        raise ValueError("current snapshot pointer is unreadable") from exc


def _baseline_from_raw(raw: bytes | None) -> CurrentPointerBaseline:
    if raw is None:
        return CurrentPointerBaseline(
            build_id=None,
            pointer_sha256=None,
        )
    return CurrentPointerBaseline(
        build_id=_parse_pointer_bytes(raw),
        pointer_sha256=hashlib.sha256(raw).hexdigest(),
    )


def read_current_pointer_baseline(
    snapshot_root: Path,
) -> CurrentPointerBaseline:
    """Read the exact current pointer bytes for a later locked CAS."""

    root = snapshot_root.resolve()
    return _baseline_from_raw(_read_pointer_bytes(root))


def _ensure_lock_byte(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    handle.seek(0)


def _try_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(
            handle.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _is_link_junction_or_reparse(path: Path) -> bool:
    try:
        attributes = int(
            getattr(path.lstat(), "st_file_attributes", 0)
        )
    except FileNotFoundError:
        attributes = 0
    reparse_flag = int(
        getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    )
    return (
        path.is_symlink()
        or path.is_junction()
        or bool(attributes & reparse_flag)
    )


@contextmanager
def _current_pointer_lock(
    snapshot_root: Path,
    *,
    timeout_seconds: float,
):
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds < 0
    ):
        raise ValueError(
            "pointer lock timeout must be a finite nonnegative number"
        )
    lock_path = snapshot_root / CURRENT_POINTER_LOCK_NAME
    if _is_link_junction_or_reparse(lock_path):
        raise ValueError(
            "current pointer lock cannot be a link or reparse point"
        )
    deadline = time.monotonic() + timeout_seconds
    with lock_path.open("a+b") as handle:
        _ensure_lock_byte(handle)
        while True:
            try:
                _try_lock(handle)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise PointerCASLockTimeoutError(
                        "timed out acquiring the current pointer lock"
                    ) from exc
                time.sleep(_LOCK_RETRY_SECONDS)
        try:
            yield
        finally:
            try:
                _unlock(handle)
            except OSError:
                # Closing the handle still releases an OS-owned lock.  Do not
                # obscure the already established pointer outcome.
                pass


def _read_validated_destination_snapshot(
    snapshot_root: Path,
    target_build_id: str,
    *,
    expected_manifest_sha256: str | None = None,
) -> tuple[Path, bytes, str]:
    snapshots_root = snapshot_root / "snapshots"
    if _is_link_junction_or_reparse(snapshots_root):
        raise PointerCASDestinationError(
            "immutable snapshots root cannot be a link or reparse point"
        )
    if not snapshots_root.is_dir():
        raise PointerCASDestinationError(
            "immutable snapshots directory does not exist"
        )
    destination_path = snapshots_root / target_build_id
    try:
        if _is_link_junction_or_reparse(destination_path):
            raise PointerCASDestinationError(
                "pointer destination cannot be a link or junction"
            )
        resolved_snapshots = snapshots_root.resolve(strict=True)
        if resolved_snapshots.parent != snapshot_root:
            raise PointerCASDestinationError(
                "immutable snapshots root escapes the snapshot root"
            )
        destination = destination_path.resolve(strict=True)
    except OSError as exc:
        raise PointerCASDestinationError(
            f"pointer destination does not exist: {target_build_id}"
        ) from exc
    if (
        not destination.is_dir()
        or destination.parent != resolved_snapshots
    ):
        raise PointerCASDestinationError(
            "pointer destination must be a direct immutable snapshot child"
        )
    manifest_path = destination / "manifest.json"
    try:
        if _is_link_junction_or_reparse(manifest_path):
            raise PointerCASDestinationError(
                "pointer destination manifest cannot be a link or junction"
            )
        resolved_manifest = manifest_path.resolve(strict=True)
    except OSError as exc:
        raise PointerCASDestinationError(
            "pointer destination manifest is unavailable"
        ) from exc
    if (
        not resolved_manifest.is_file()
        or resolved_manifest.parent != destination
    ):
        raise PointerCASDestinationError(
            "pointer destination manifest escapes the snapshot"
        )
    try:
        manifest_bytes = _read_bounded_file(
            resolved_manifest,
            maximum_bytes=_MAX_MANIFEST_BYTES,
            label="pointer destination manifest",
        )
    except (OSError, ValueError) as exc:
        raise PointerCASDestinationError(str(exc)) from exc
    try:
        manifest = _strict_json_object(
            manifest_bytes,
            label="pointer destination manifest",
            maximum_bytes=_MAX_MANIFEST_BYTES,
        )
    except ValueError as exc:
        raise PointerCASDestinationError(str(exc)) from exc
    if (
        manifest.get("schema") != SNAPSHOT_SCHEMA
        or manifest.get("buildId") != target_build_id
    ):
        raise PointerCASDestinationError(
            "pointer destination manifest identity is invalid"
        )
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if (
        expected_manifest_sha256 is not None
        and manifest_sha256 != expected_manifest_sha256
    ):
        raise PointerCASDestinationError(
            "pointer destination manifest SHA-256 changed"
        )
    return destination, manifest_bytes, manifest_sha256


def _validate_destination(
    snapshot_root: Path,
    target_build_id: str,
    *,
    expected_manifest_sha256: str | None = None,
) -> str:
    _destination, _manifest_bytes, manifest_sha256 = (
        _read_validated_destination_snapshot(
            snapshot_root,
            target_build_id,
            expected_manifest_sha256=expected_manifest_sha256,
        )
    )
    return manifest_sha256


def _canonical_pointer_bytes(build_id: str) -> bytes:
    return (
        json.dumps(
            {
                "buildId": build_id,
                "snapshotRelativePath": f"snapshots/{build_id}",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _verified_receipt(
    *,
    operation: str,
    before: CurrentPointerBaseline,
    after: CurrentPointerBaseline,
    pointer_updated: bool,
    replaced: bool,
) -> dict[str, object]:
    return {
        "schema": POINTER_CAS_RECEIPT_SCHEMA,
        "status": "VERIFIED" if replaced else "VERIFIED_NOOP",
        "operation": operation,
        "evidenceClass": "UNSIGNED_LOCAL_WRITE_FACT",
        "beforeBuildId": before.build_id,
        "afterBuildId": after.build_id,
        "beforePointerSha256": before.pointer_sha256,
        "afterPointerSha256": after.pointer_sha256,
        "pointerUpdated": pointer_updated,
        "verifiedAfterReplace": replaced,
        "verifiedUnderLock": True,
    }


def _not_replaced_receipt(
    *,
    operation: str,
    before: CurrentPointerBaseline,
    intended_build_id: str,
) -> dict[str, object]:
    return {
        "schema": POINTER_CAS_RECEIPT_SCHEMA,
        "status": "NOT_REPLACED",
        "operation": operation,
        "evidenceClass": "UNSIGNED_LOCAL_WRITE_FACT",
        "beforeBuildId": before.build_id,
        "intendedAfterBuildId": intended_build_id,
        "beforePointerSha256": before.pointer_sha256,
        "pointerUpdated": False,
        "verifiedAfterReplace": False,
        "verifiedUnderLock": True,
    }


def _uncertain_receipt(
    *,
    snapshot_root: Path,
    operation: str,
    before: CurrentPointerBaseline,
    intended_build_id: str,
    intended_sha256: str,
) -> dict[str, object]:
    observed_build_id: str | None = None
    observed_sha256: str | None = None
    observation_error: str | None = None
    try:
        observed_raw = _read_pointer_bytes(snapshot_root)
        if observed_raw is not None:
            observed_sha256 = hashlib.sha256(observed_raw).hexdigest()
            observed_build_id = _parse_pointer_bytes(observed_raw)
    except (OSError, ValueError) as exc:
        observation_error = str(exc)
    observed_matches = (
        observed_build_id == intended_build_id
        and observed_sha256 == intended_sha256
    )
    return {
        "schema": POINTER_CAS_RECEIPT_SCHEMA,
        "status": "UNCERTAIN_AFTER_REPLACE_ATTEMPT",
        "operation": operation,
        "evidenceClass": "UNSIGNED_LOCAL_WRITE_FACT",
        "beforeBuildId": before.build_id,
        "intendedAfterBuildId": intended_build_id,
        "beforePointerSha256": before.pointer_sha256,
        "intendedAfterPointerSha256": intended_sha256,
        "observedBuildId": observed_build_id,
        "observedPointerSha256": observed_sha256,
        "observedMatchesIntended": observed_matches,
        "observationError": observation_error,
        "pointerUpdated": None,
        "verifiedAfterReplace": False,
        "verifiedUnderLock": True,
    }


def _atomic_replace_with_windows_retry(
    source: Path,
    destination: Path,
) -> None:
    """Bound transient Windows reader sharing violations."""

    deadline = time.monotonic() + _WINDOWS_REPLACE_TIMEOUT_SECONDS
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            # A successful atomic rename consumes ``source``.  Its continued
            # existence lets us safely retry a Windows sharing violation.
            if not source.exists():
                raise
            if time.monotonic() >= deadline:
                raise _ReplaceNotPerformedError(
                    "current pointer replace remained blocked by a "
                    "Windows sharing violation"
                ) from exc
            time.sleep(_WINDOWS_REPLACE_RETRY_SECONDS)


def _validate_optional_sha256(
    value: str | None,
    *,
    label: str,
) -> None:
    if value is not None and (
        type(value) is not str
        or len(value) != 64
        or any(
            character not in "0123456789abcdef"
            for character in value
        )
    ):
        raise ValueError(
            f"{label} must be lowercase hexadecimal SHA-256"
        )


def _locked_expected_pointer(
    snapshot_root: Path,
    expected: CurrentPointerBaseline,
) -> CurrentPointerBaseline:
    observed = _baseline_from_raw(_read_pointer_bytes(snapshot_root))
    if observed != expected:
        raise PointerCASConflictError(
            "current pointer changed before locked CAS "
            f"(expected buildId={expected.build_id!r}, "
            f"sha256={expected.pointer_sha256!r}; "
            f"observed buildId={observed.build_id!r}, "
            f"sha256={observed.pointer_sha256!r})"
        )
    return observed


def capture_current_snapshot_baseline(
    snapshot_root: Path,
    *,
    lock_timeout_seconds: float = _DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> CurrentSnapshotBaseline:
    """Freeze current pointer and manifest identity under the shared lock."""

    root = snapshot_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    with _current_pointer_lock(
        root,
        timeout_seconds=lock_timeout_seconds,
    ):
        pointer = _baseline_from_raw(_read_pointer_bytes(root))
        if pointer.build_id is None:
            raise PointerCASDestinationError(
                "current snapshot pointer is not available"
            )
        snapshot_dir, manifest_bytes, manifest_sha256 = (
            _read_validated_destination_snapshot(
                root,
                pointer.build_id,
            )
        )
        _locked_expected_pointer(root, pointer)
        (
            verified_snapshot_dir,
            verified_manifest_bytes,
            verified_manifest_sha256,
        ) = _read_validated_destination_snapshot(
            root,
            pointer.build_id,
            expected_manifest_sha256=manifest_sha256,
        )
        if (
            verified_snapshot_dir != snapshot_dir
            or verified_manifest_bytes != manifest_bytes
            or verified_manifest_sha256 != manifest_sha256
        ):
            raise PointerCASDestinationError(
                "current snapshot manifest changed during capture"
            )
        _locked_expected_pointer(root, pointer)
        return CurrentSnapshotBaseline(
            pointer=pointer,
            snapshot_dir=snapshot_dir,
            manifest_bytes=manifest_bytes,
            manifest_sha256=manifest_sha256,
        )


def validate_current_pointer_destination(
    *,
    snapshot_root: Path,
    target_build_id: str,
    expected: CurrentPointerBaseline,
    expected_current_manifest_sha256: str | None = None,
    expected_target_manifest_sha256: str | None = None,
    operation: str = "POINTER_VALIDATION",
    lock_timeout_seconds: float = _DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Validate one target against an exact current pointer without writing."""

    root = snapshot_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    target_build_id = _safe_build_id(target_build_id)
    if not isinstance(expected, CurrentPointerBaseline):
        raise TypeError("expected pointer baseline is required")
    if not _SAFE_OPERATION.fullmatch(operation):
        raise ValueError(
            "pointer validation operation must be an uppercase "
            "ASCII identifier"
        )
    _validate_optional_sha256(
        expected_current_manifest_sha256,
        label="expected current manifest SHA-256",
    )
    _validate_optional_sha256(
        expected_target_manifest_sha256,
        label="expected target manifest SHA-256",
    )

    with _current_pointer_lock(
        root,
        timeout_seconds=lock_timeout_seconds,
    ):
        before = _locked_expected_pointer(root, expected)
        if expected_current_manifest_sha256 is not None:
            if before.build_id is None:
                raise PointerCASDestinationError(
                    "expected current manifest has no current build"
                )
            _validate_destination(
                root,
                before.build_id,
                expected_manifest_sha256=(
                    expected_current_manifest_sha256
                ),
            )
        target_manifest_sha256 = _validate_destination(
            root,
            target_build_id,
            expected_manifest_sha256=expected_target_manifest_sha256,
        )
        after = _locked_expected_pointer(root, expected)
        if expected_current_manifest_sha256 is not None:
            if after.build_id is None:
                raise PointerCASDestinationError(
                    "expected current manifest has no current build"
                )
            _validate_destination(
                root,
                after.build_id,
                expected_manifest_sha256=(
                    expected_current_manifest_sha256
                ),
            )
        _validate_destination(
            root,
            target_build_id,
            expected_manifest_sha256=target_manifest_sha256,
        )
        receipt = _verified_receipt(
            operation=operation,
            before=before,
            after=after,
            pointer_updated=False,
            replaced=False,
        )
        receipt["validatedTargetBuildId"] = target_build_id
        receipt["validatedTargetManifestSha256"] = (
            target_manifest_sha256
        )
        return receipt


def validate_current_snapshot_baseline(
    *,
    snapshot_root: Path,
    baseline: CurrentSnapshotBaseline,
    operation: str = "UPDATE_BASELINE_VALIDATION",
    lock_timeout_seconds: float = _DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Recheck an exact captured current pointer and manifest without writes."""

    if type(baseline) is not CurrentSnapshotBaseline:
        raise TypeError("current snapshot baseline is required")
    root = snapshot_root.resolve()
    expected_dir = (
        root / "snapshots" / str(baseline.pointer.build_id)
    ).resolve()
    if baseline.snapshot_dir != expected_dir:
        raise PointerCASDestinationError(
            "snapshot baseline path does not match the snapshot root"
        )
    return validate_current_pointer_destination(
        snapshot_root=root,
        target_build_id=str(baseline.pointer.build_id),
        expected=baseline.pointer,
        expected_current_manifest_sha256=baseline.manifest_sha256,
        expected_target_manifest_sha256=baseline.manifest_sha256,
        operation=operation,
        lock_timeout_seconds=lock_timeout_seconds,
    )


def compare_and_swap_current_pointer(
    *,
    snapshot_root: Path,
    target_build_id: str,
    expected: CurrentPointerBaseline,
    expected_current_manifest_sha256: str | None = None,
    expected_target_manifest_sha256: str | None = None,
    operation: str = "POINTER_CAS",
    lock_timeout_seconds: float = _DEFAULT_LOCK_TIMEOUT_SECONDS,
    fault_injector: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Atomically replace ``current.json`` under a shared raw-bytes CAS."""

    root = snapshot_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    target_build_id = _safe_build_id(target_build_id)
    if not isinstance(expected, CurrentPointerBaseline):
        raise TypeError("expected pointer baseline is required")
    if not _SAFE_OPERATION.fullmatch(operation):
        raise ValueError(
            "pointer CAS operation must be an uppercase ASCII identifier"
        )
    _validate_optional_sha256(
        expected_current_manifest_sha256,
        label="expected current manifest SHA-256",
    )
    _validate_optional_sha256(
        expected_target_manifest_sha256,
        label="expected target manifest SHA-256",
    )
    pointer_path = root / CURRENT_POINTER_NAME
    intended_bytes = _canonical_pointer_bytes(target_build_id)
    intended_sha256 = hashlib.sha256(intended_bytes).hexdigest()

    with _current_pointer_lock(
        root,
        timeout_seconds=lock_timeout_seconds,
    ):
        before = _locked_expected_pointer(root, expected)
        if expected_current_manifest_sha256 is not None:
            if before.build_id is None:
                raise PointerCASDestinationError(
                    "expected current manifest has no current build"
                )
            _validate_destination(
                root,
                before.build_id,
                expected_manifest_sha256=(
                    expected_current_manifest_sha256
                ),
            )
        locked_target_manifest_sha256 = _validate_destination(
            root,
            target_build_id,
            expected_manifest_sha256=expected_target_manifest_sha256,
        )
        if before.build_id == target_build_id:
            return _verified_receipt(
                operation=operation,
                before=before,
                after=before,
                pointer_updated=False,
                replaced=False,
            )

        temporary_path: Path | None = None
        try:
            try:
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{CURRENT_POINTER_NAME}.",
                    suffix=".tmp",
                    dir=root,
                )
                temporary_path = Path(temporary_name)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(intended_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                if fault_injector is not None:
                    fault_injector("before_replace")
            except Exception as exc:
                raise PointerCASWriteError(
                    "current pointer write failed before atomic replace",
                    receipt=_not_replaced_receipt(
                        operation=operation,
                        before=before,
                        intended_build_id=target_build_id,
                    ),
                ) from exc

            _validate_destination(
                root,
                target_build_id,
                expected_manifest_sha256=(
                    locked_target_manifest_sha256
                ),
            )
            try:
                _atomic_replace_with_windows_retry(
                    temporary_path,
                    pointer_path,
                )
            except _ReplaceNotPerformedError as exc:
                raise PointerCASWriteError(
                    "current pointer atomic replace was not performed",
                    receipt=_not_replaced_receipt(
                        operation=operation,
                        before=before,
                        intended_build_id=target_build_id,
                    ),
                ) from exc
            except Exception as exc:
                raise PointerCASUncertainStateError(
                    "current pointer state is uncertain after atomic "
                    "replace was attempted",
                    receipt=_uncertain_receipt(
                        snapshot_root=root,
                        operation=operation,
                        before=before,
                        intended_build_id=target_build_id,
                        intended_sha256=intended_sha256,
                    ),
                ) from exc
            temporary_path = None

            try:
                if fault_injector is not None:
                    fault_injector("after_replace")
                observed_raw = _read_pointer_bytes(root)
                observed = _baseline_from_raw(observed_raw)
                _validate_destination(
                    root,
                    target_build_id,
                    expected_manifest_sha256=(
                        locked_target_manifest_sha256
                    ),
                )
                if (
                    observed_raw != intended_bytes
                    or observed.build_id != target_build_id
                    or observed.pointer_sha256 != intended_sha256
                ):
                    raise ValueError(
                        "verified pointer differs from intended bytes"
                    )
            except Exception as exc:
                raise PointerCASUncertainStateError(
                    "current pointer state is uncertain after atomic "
                    "replace was attempted",
                    receipt=_uncertain_receipt(
                        snapshot_root=root,
                        operation=operation,
                        before=before,
                        intended_build_id=target_build_id,
                        intended_sha256=intended_sha256,
                    ),
                ) from exc
            return _verified_receipt(
                operation=operation,
                before=before,
                after=observed,
                pointer_updated=True,
                replaced=True,
            )
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    # Cleanup failure must not turn a typed write outcome into
                    # a false pointer-state claim.
                    pass
