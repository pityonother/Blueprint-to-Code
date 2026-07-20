"""Persistent, source-fingerprinted cache for expensive resource-node decoding."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable


SCAN_CACHE_SCHEMA = "ark-resource-node-scan-cache/v2"
RESOURCE_NODE_EXTRACTOR_VERSION = "resource-node-uasset-parser/v4-foliage-actor"


class ResourceNodeScanCache:
    """Reuse decoded nodes only while the source size and mtime are unchanged."""

    def __init__(
        self,
        path: Path,
        *,
        refresh: bool = False,
        extractor_version: str = RESOURCE_NODE_EXTRACTOR_VERSION,
        verify_content_hash: bool = True,
    ) -> None:
        self.path = Path(path)
        self.refresh = bool(refresh)
        self.extractor_version = str(extractor_version)
        self.verify_content_hash = bool(verify_content_hash)
        self._entries: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self.hits = 0
        self.misses = 0
        self.invalidated = 0
        self.load_status = "NOT_FOUND"
        self._load()

    @staticmethod
    def _key(path: Path) -> str:
        return str(path.resolve()).casefold()

    def _fingerprint(self, path: Path) -> dict[str, int | str]:
        stat = path.stat()
        fingerprint: dict[str, int | str] = {
            "sizeBytes": int(stat.st_size),
            "mtimeNs": int(stat.st_mtime_ns),
        }
        if self.verify_content_hash:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            fingerprint["contentSha256"] = digest.hexdigest()
        return fingerprint

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            self.load_status = "INVALID_IGNORED"
            return
        if not isinstance(payload, dict) or payload.get("schema") != SCAN_CACHE_SCHEMA:
            self.load_status = "INVALID_IGNORED"
            return
        expected_policy = "SHA256" if self.verify_content_hash else "STAT_ONLY"
        if (
            payload.get("extractorVersion") != self.extractor_version
            or payload.get("contentHashPolicy") != expected_policy
        ):
            self.load_status = "VERSION_MISMATCH_IGNORED"
            return
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            self.load_status = "INVALID_IGNORED"
            return
        self._entries = {
            str(key): value for key, value in entries.items() if isinstance(value, dict)
        }
        self.load_status = "LOADED"

    def get_or_extract(
        self,
        path: Path,
        extractor: Callable[[Path], dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        resolved = Path(path).resolve()
        fingerprint = self._fingerprint(resolved)
        key = self._key(resolved)
        cached = self._entries.get(key)
        if not self.refresh and isinstance(cached, dict):
            node = cached.get("node")
            if (
                all(cached.get(key) == value for key, value in fingerprint.items())
                and isinstance(node, dict)
            ):
                self.hits += 1
                return deepcopy(node), True
            self.invalidated += 1

        self.misses += 1
        node = extractor(resolved)
        if not isinstance(node, dict):
            raise TypeError("resource-node extractor must return a dictionary")
        self._entries[key] = {
            "path": str(resolved),
            **fingerprint,
            "node": deepcopy(node),
        }
        self._dirty = True
        return deepcopy(node), False

    def flush(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "schema": SCAN_CACHE_SCHEMA,
                    "extractorVersion": self.extractor_version,
                    "contentHashPolicy": "SHA256" if self.verify_content_hash else "STAT_ONLY",
                    "entries": self._entries,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
        self._dirty = False

    def coverage(self) -> dict[str, Any]:
        return {
            "status": self.load_status,
            "path": str(self.path.resolve()),
            "entries": len(self._entries),
            "hits": self.hits,
            "misses": self.misses,
            "invalidated": self.invalidated,
            "refresh": self.refresh,
            "extractorVersion": self.extractor_version,
            "contentHashPolicy": "SHA256" if self.verify_content_hash else "STAT_ONLY",
        }
