"""Persistent source-fingerprinted cache for projected creature Blueprint facts."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable


SCAN_CACHE_SCHEMA = "ark-creature-asset-scan-cache/v1"
CREATURE_ASSET_EXTRACTOR_VERSION = "creature-blueprint-projection/v1"


class CreatureAssetScanCache:
    """Reuse a projected asset fact only when both metadata and bytes still match."""

    def __init__(
        self,
        path: Path,
        *,
        refresh: bool = False,
        extractor_version: str = CREATURE_ASSET_EXTRACTOR_VERSION,
    ) -> None:
        self.path = Path(path)
        self.refresh = bool(refresh)
        self.extractor_version = str(extractor_version)
        self._entries: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self.hits = 0
        self.misses = 0
        self.invalidated = 0
        self.load_status = "NOT_FOUND"
        self._load()

    @staticmethod
    def _key(path: Path) -> str:
        return str(Path(path).resolve()).casefold()

    @staticmethod
    def _fingerprint(path: Path) -> dict[str, int | str]:
        resolved = Path(path).resolve()
        stat = resolved.stat()
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {
            "sizeBytes": int(stat.st_size),
            "mtimeNs": int(stat.st_mtime_ns),
            "contentSha256": digest.hexdigest(),
        }

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
        if payload.get("extractorVersion") != self.extractor_version:
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
            fact = cached.get("fact")
            if (
                all(cached.get(name) == value for name, value in fingerprint.items())
                and isinstance(fact, dict)
            ):
                self.hits += 1
                return deepcopy(fact), True
            self.invalidated += 1

        self.misses += 1
        fact = extractor(resolved)
        if not isinstance(fact, dict):
            raise TypeError("creature asset extractor must return a dictionary")
        self._entries[key] = {
            "path": str(resolved),
            **fingerprint,
            "fact": deepcopy(fact),
        }
        self._dirty = True
        return deepcopy(fact), False

    def content_sha256(self, path: Path) -> str | None:
        entry = self._entries.get(self._key(path))
        value = entry.get("contentSha256") if isinstance(entry, dict) else None
        return str(value) if isinstance(value, str) and len(value) == 64 else None

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
                    "contentHashPolicy": "SHA256",
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
            "contentHashPolicy": "SHA256",
        }
