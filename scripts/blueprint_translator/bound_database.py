"""File-descriptor-bound byte snapshots for immutable SQLite evidence."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path


_FILE_ATTRIBUTE_REPARSE_POINT = int(
    getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
)


class BoundDatabaseError(ValueError):
    """A database path could not be bound to one safe byte snapshot."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        self.detail = str(message)
        super().__init__(f"{self.code}: {self.detail}")


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(getattr(metadata, "st_nlink", 1)),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(getattr(metadata, "st_file_attributes", 0)),
    )


def _plain_file_metadata(path: Path, *, label: str) -> os.stat_result:
    components: list[Path] = []
    current = path
    while current != current.parent:
        components.append(current)
        current = current.parent
    for component in reversed(components):
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            raise FileNotFoundError(component) from None
        is_reparse = bool(
            int(getattr(metadata, "st_file_attributes", 0))
            & _FILE_ATTRIBUTE_REPARSE_POINT
        )
        if component != path:
            if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISDIR(
                metadata.st_mode
            ):
                raise BoundDatabaseError(
                    "DATABASE_PATH_INVALID",
                    f"{label} path traverses a link, reparse point, or special directory",
                )
            continue
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or is_reparse
        ):
            raise BoundDatabaseError(
                "DATABASE_FILE_INVALID",
                f"{label} must be one plain regular file",
            )
        if int(getattr(metadata, "st_nlink", 1)) != 1:
            raise BoundDatabaseError(
                "HARDLINK_REJECTED",
                f"{label} must not have hard-link aliases",
            )
        return metadata
    raise FileNotFoundError(path)


def _reject_sidecars(path: Path, *, label: str) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        try:
            sidecar.lstat()
        except FileNotFoundError:
            continue
        raise BoundDatabaseError(
            "SQLITE_SIDECAR_PRESENT",
            f"{label} sidecar is forbidden: {path.name}{suffix}",
        )


@dataclass
class BoundDatabaseSnapshot:
    """Own bytes copied from one verified source file descriptor."""

    source_path: Path
    data: bytes
    sha256: str
    size_bytes: int
    _closed: bool = False

    def verify(self) -> None:
        if self._closed:
            raise RuntimeError("bound database snapshot is closed")
        if len(self.data) != self.size_bytes:
            raise BoundDatabaseError(
                "DATABASE_SNAPSHOT_CHANGED",
                "database snapshot size changed",
            )
        if hashlib.sha256(self.data).hexdigest() != self.sha256:
            raise BoundDatabaseError(
                "DATABASE_SNAPSHOT_CHANGED",
                "database snapshot hash changed",
            )
        if (
            len(self.data) < 100
            or self.data[:16] != b"SQLite format 3\x00"
        ):
            raise BoundDatabaseError(
                "SQLITE_HEADER_INVALID",
                "database snapshot has an invalid SQLite header",
            )
        if self.data[18] != 1 or self.data[19] != 1:
            raise BoundDatabaseError(
                "SQLITE_JOURNAL_INVALID",
                "evidence database must use rollback/DELETE journal format",
            )

    def open_connection(self) -> sqlite3.Connection:
        """Deserialize the bound bytes and verify the live SQLite handle."""

        self.verify()
        connection = sqlite3.connect(":memory:")
        try:
            connection.deserialize(self.data)
            serialized = connection.serialize()
            if (
                len(serialized) != self.size_bytes
                or hashlib.sha256(serialized).hexdigest() != self.sha256
            ):
                raise BoundDatabaseError(
                    "SQLITE_CONNECTION_BINDING_FAILED",
                    "SQLite connection bytes differ from the bound snapshot",
                )
            return connection
        except Exception:
            connection.close()
            raise

    def close(self) -> None:
        if not self._closed:
            self.data = b""
            self._closed = True

    def __enter__(self) -> BoundDatabaseSnapshot:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


def materialize_bound_database_snapshot(
    database_path: str | Path,
    *,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
    label: str = "evidence database",
) -> BoundDatabaseSnapshot:
    """Copy one stable source fd into memory and bind its exact bytes."""

    path = Path(os.path.abspath(os.path.expanduser(os.fspath(database_path))))
    before = _plain_file_metadata(path, label=label)
    _reject_sidecars(path, label=label)
    if expected_size is not None and int(before.st_size) != int(expected_size):
        raise BoundDatabaseError(
            "DATABASE_SIZE_MISMATCH",
            f"{label} size differs from its binding",
        )

    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise BoundDatabaseError(
                "DATABASE_CHANGED",
                f"{label} changed while its file descriptor was opened",
            )
        digest = hashlib.sha256()
        copied = 0
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            digest.update(chunk)
            chunks.append(chunk)
        opened_after = os.fstat(descriptor)
        after = _plain_file_metadata(path, label=label)
        _reject_sidecars(path, label=label)
        if (
            _identity(opened) != _identity(opened_after)
            or _identity(before) != _identity(after)
            or copied != int(opened.st_size)
        ):
            raise BoundDatabaseError(
                "DATABASE_CHANGED",
                f"{label} changed while its bytes were copied",
            )
        observed_sha256 = digest.hexdigest()
        if expected_sha256 is not None and observed_sha256 != expected_sha256:
            raise BoundDatabaseError(
                "DATABASE_HASH_MISMATCH",
                f"{label} hash differs from its binding",
            )
        snapshot = BoundDatabaseSnapshot(
            source_path=path,
            data=b"".join(chunks),
            sha256=observed_sha256,
            size_bytes=copied,
        )
        snapshot.verify()
        return snapshot
    finally:
        if descriptor is not None:
            os.close(descriptor)


__all__ = [
    "BoundDatabaseError",
    "BoundDatabaseSnapshot",
    "materialize_bound_database_snapshot",
]
