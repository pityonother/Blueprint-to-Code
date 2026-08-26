"""Bind one Unreal package generation for direct binary Evidence reads."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .evidence_publication import (
    _lexical_absolute,
    _require_plain_file,
    _require_plain_path_chain,
)


SOURCE_CHANGED_CODE = "EVIDENCE_SOURCE_CHANGED_DURING_CAPTURE"
_COMPANION_SUFFIXES = (".uexp", ".ubulk")


class EvidenceSourceChangedDuringCapture(RuntimeError):
    """Raised when the live package no longer matches the parsed snapshot."""

    code = SOURCE_CHANGED_CODE

    def __init__(self, detail: str) -> None:
        super().__init__(f"{self.code}: {detail}")


@dataclass(frozen=True)
class SourceFileObservation:
    suffix: str
    path: Path
    size_bytes: int
    sha256: str
    identity: tuple[int, int]


@dataclass
class PackageSourceSnapshot:
    original_uasset_path: Path
    snapshot_uasset_path: Path
    observations: tuple[SourceFileObservation, ...]
    _temporary: tempfile.TemporaryDirectory[str]

    def close(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> "PackageSourceSnapshot":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def logical_path(self, suffix: str) -> Path | None:
        for observation in self.observations:
            if observation.suffix == suffix:
                return observation.path
        return None

    def assert_original_unchanged(self) -> None:
        current = _observe_package(self.original_uasset_path)
        expected_by_suffix = {item.suffix: item for item in self.observations}
        current_by_suffix = {item.suffix: item for item in current}
        if set(current_by_suffix) != set(expected_by_suffix):
            raise EvidenceSourceChangedDuringCapture(
                "the package companion file set changed"
            )
        for suffix, expected in expected_by_suffix.items():
            observed = current_by_suffix[suffix]
            if (
                observed.identity != expected.identity
                or observed.size_bytes != expected.size_bytes
                or observed.sha256 != expected.sha256
            ):
                raise EvidenceSourceChangedDuringCapture(
                    f"the live {suffix} file changed after the package snapshot"
                )


def _hash_plain_file(path: Path) -> SourceFileObservation:
    _require_plain_path_chain(path, label="Unreal package source")
    _require_plain_file(path, label="Unreal package source")
    before = path.lstat()
    if int(getattr(before, "st_nlink", 1)) != 1:
        raise ValueError("Unreal package source cannot be a hard link")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("Unreal package source must be a regular file")
        if (int(opened.st_dev), int(opened.st_ino)) != (
            int(before.st_dev),
            int(before.st_ino),
        ):
            raise EvidenceSourceChangedDuringCapture(
                "the package source identity changed before it was read"
            )
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        opened_after = os.fstat(stream.fileno())
    after = path.lstat()
    identities = {
        (int(before.st_dev), int(before.st_ino)),
        (int(opened.st_dev), int(opened.st_ino)),
        (int(opened_after.st_dev), int(opened_after.st_ino)),
        (int(after.st_dev), int(after.st_ino)),
    }
    if len(identities) != 1 or size != int(opened.st_size) or size != int(after.st_size):
        raise EvidenceSourceChangedDuringCapture(
            "the package source changed while it was hashed"
        )
    return SourceFileObservation(
        suffix=path.suffix.lower(),
        path=path,
        size_bytes=size,
        sha256=digest.hexdigest(),
        identity=next(iter(identities)),
    )


def _observe_package(uasset_path: Path) -> tuple[SourceFileObservation, ...]:
    observations: list[SourceFileObservation] = []
    primary_suffix = ".umap" if uasset_path.suffix.casefold() == ".umap" else ".uasset"
    for suffix in (primary_suffix, *_COMPANION_SUFFIXES):
        candidate = uasset_path.with_suffix(suffix)
        try:
            candidate.lstat()
        except FileNotFoundError:
            if suffix == primary_suffix:
                raise
            continue
        observations.append(_hash_plain_file(candidate))
    return tuple(observations)


def snapshot_package_source(uasset_path: str | os.PathLike[str]) -> PackageSourceSnapshot:
    """Copy a stable package generation and retain its original-file binding."""

    original = _lexical_absolute(uasset_path)
    initial = _observe_package(original)
    temporary = tempfile.TemporaryDirectory(prefix="ark-uasset-snapshot-")
    snapshot_root = Path(temporary.name)
    snapshot_uasset = snapshot_root / original.name
    try:
        for observation in initial:
            destination = snapshot_root / observation.path.name
            shutil.copyfile(observation.path, destination)
            copied = _hash_plain_file(destination)
            if (
                copied.size_bytes != observation.size_bytes
                or copied.sha256 != observation.sha256
            ):
                raise EvidenceSourceChangedDuringCapture(
                    f"the copied {observation.suffix} bytes do not match the source observation"
                )
        result = PackageSourceSnapshot(
            original_uasset_path=original,
            snapshot_uasset_path=snapshot_uasset,
            observations=initial,
            _temporary=temporary,
        )
        result.assert_original_unchanged()
        return result
    except Exception:
        temporary.cleanup()
        raise
