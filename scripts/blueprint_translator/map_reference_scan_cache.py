"""Incremental cache for expensive direct ``.umap`` package-token scans.

The cache fingerprint deliberately uses only file ``size`` and ``mtime_ns``.
It does *not* read or hash map contents on a cache hit, because avoiding those
multi-gigabyte reads is the purpose of this cache.  A DevKit update that changes
either stat field invalidates that map entry.  Consequently this cache is a
performance optimization, never independent evidence that bytes are unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable


MAP_REFERENCE_SCAN_CACHE_SCHEMA = "ark-map-reference-scan-cache/v1"
MAP_REFERENCE_EXTRACTOR_VERSION = "serialized-unreal-package-token/v1"
FINGERPRINT_POLICY = "FILE_STAT_SIZE_MTIME_NS"


def _normalized_packages(packages: Iterable[str]) -> frozenset[str]:
    return frozenset(
        normalized
        for package in packages
        if (normalized := str(package).strip().casefold())
    )


def node_package_set_revision(node_packages: Iterable[str]) -> str:
    """Return an order/case/duplicate-independent revision for a package set."""

    canonical = "\n".join(sorted(_normalized_packages(node_packages))).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class MapReferenceScanCache:
    """Cache the exact node-package matches found in each map file.

    ``node_packages`` is part of the cache context.  If that set changes, every
    stored map result is invalid because a formerly irrelevant token may now be
    a resource node.  ``extractor_version`` similarly invalidates all results
    when token extraction semantics change.
    """

    def __init__(
        self,
        path: Path,
        *,
        node_packages: Iterable[str],
        refresh: bool = False,
        extractor_version: str = MAP_REFERENCE_EXTRACTOR_VERSION,
        checkpoint_every: int = 0,
    ) -> None:
        self.path = Path(path)
        self.refresh = bool(refresh)
        self.extractor_version = str(extractor_version)
        self.checkpoint_every = max(0, int(checkpoint_every))
        self.node_packages = _normalized_packages(node_packages)
        self.node_package_set_revision = node_package_set_revision(self.node_packages)

        self._entries: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._pending_since_checkpoint = 0
        self.hits = 0
        self.misses = 0
        self.invalidated = 0
        self.refresh_bypasses = 0
        self.load_status = "NOT_FOUND"
        self._load()

    @staticmethod
    def _key(path: Path) -> str:
        return str(Path(path).resolve()).casefold()

    @staticmethod
    def _fingerprint(path: Path) -> dict[str, int]:
        stat = Path(path).stat()
        return {
            "sizeBytes": int(stat.st_size),
            "mtimeNs": int(stat.st_mtime_ns),
        }

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            self.load_status = "INVALID_IGNORED"
            return
        if not isinstance(payload, dict) or payload.get("schema") != MAP_REFERENCE_SCAN_CACHE_SCHEMA:
            self.load_status = "INVALID_IGNORED"
            return

        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, dict):
            self.load_status = "INVALID_IGNORED"
            return
        entries = {
            str(key): value
            for key, value in raw_entries.items()
            if isinstance(value, dict)
        }
        if (
            payload.get("extractorVersion") != self.extractor_version
            or payload.get("nodePackageSetRevision")
            != self.node_package_set_revision
            or payload.get("fingerprintPolicy") != FINGERPRINT_POLICY
        ):
            self.invalidated += len(entries)
            self.load_status = "CONTEXT_MISMATCH_IGNORED"
            return

        self._entries = entries
        self.load_status = "LOADED"

    def get_or_scan(
        self,
        map_path: Path,
        scanner: Callable[[Path], Iterable[str]],
    ) -> tuple[set[str], bool]:
        """Return exact current-node matches and whether they came from cache."""

        resolved = Path(map_path).resolve()
        fingerprint = self._fingerprint(resolved)
        key = self._key(resolved)
        cached = self._entries.get(key)

        if not self.refresh and isinstance(cached, dict):
            matched = cached.get("matchedNodePackages")
            valid_fingerprint = all(
                cached.get(field) == value for field, value in fingerprint.items()
            )
            valid_matches = (
                isinstance(matched, list)
                and all(isinstance(package, str) for package in matched)
                and set(matched).issubset(self.node_packages)
            )
            if valid_fingerprint and valid_matches:
                self.hits += 1
                return set(matched), True
            self.invalidated += 1
        elif self.refresh and isinstance(cached, dict):
            self.refresh_bypasses += 1

        self.misses += 1
        scanned_packages = _normalized_packages(scanner(resolved))
        exact_matches = sorted(scanned_packages.intersection(self.node_packages))
        self._entries[key] = {
            "path": str(resolved),
            **fingerprint,
            "matchedNodePackages": exact_matches,
        }
        self._dirty = True
        self._pending_since_checkpoint += 1
        if (
            self.checkpoint_every > 0
            and self._pending_since_checkpoint >= self.checkpoint_every
        ):
            self.checkpoint()
        return set(exact_matches), False

    def checkpoint(self) -> bool:
        """Atomically persist pending entries; return whether a write occurred."""

        if not self._dirty:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": MAP_REFERENCE_SCAN_CACHE_SCHEMA,
            "extractorVersion": self.extractor_version,
            "nodePackageSetRevision": self.node_package_set_revision,
            "nodePackageCount": len(self.node_packages),
            "fingerprintPolicy": FINGERPRINT_POLICY,
            "contentSha256Verified": False,
            "cachePurpose": "PERFORMANCE_ONLY",
            "entries": self._entries,
        }

        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

        self._dirty = False
        self._pending_since_checkpoint = 0
        return True

    def flush(self) -> bool:
        """Compatibility alias for callers that use ``flush`` terminology."""

        return self.checkpoint()

    def coverage(self) -> dict[str, Any]:
        """Return bounded cache telemetry and its non-cryptographic policy."""

        return {
            "status": self.load_status,
            "path": str(self.path.resolve()),
            "entries": len(self._entries),
            "hits": self.hits,
            "misses": self.misses,
            "invalidated": self.invalidated,
            "refreshBypasses": self.refresh_bypasses,
            "refresh": self.refresh,
            "checkpointEvery": self.checkpoint_every,
            "extractorVersion": self.extractor_version,
            "nodePackageSetRevision": self.node_package_set_revision,
            "nodePackageCount": len(self.node_packages),
            "fingerprintPolicy": FINGERPRINT_POLICY,
            "fingerprintFields": ["sizeBytes", "mtimeNs"],
            "contentSha256Verified": False,
            "cachePurpose": "PERFORMANCE_ONLY",
            "invalidationRule": "DEVKIT_MAP_SIZE_OR_MTIME_NS_CHANGE",
        }
