"""Path-free source identity shared by full and incremental KB builds."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Final
from urllib.parse import quote, unquote, urlsplit

from .native_ingest import NativeEvidenceSet, load_native_evidence_corpus


SOURCE_MANIFEST_SCHEMA: Final = "ark-kb-update-source-manifest/v2"
SOURCE_DIFF_SCHEMA: Final = "ark-kb-update-source-diff/v1"
SOURCE_BINDING_SCHEMA: Final = "ark-kb-source-manifest-binding/v1"
SNAPSHOT_SEMANTIC_INPUT_KEYS: Final = frozenset(
    {
        "discovery",
        "captures",
        "classHierarchyContract",
        "semanticProducerContract",
        "legacy",
        "ontology",
        "benchmarkGold",
        "qualityGold",
        "mapEvidence",
        "nativeEvidence",
    }
)
_READ_CHUNK_BYTES: Final = 8 * 1024 * 1024
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_KIND = re.compile(r"^[A-Z][A-Z0-9_]*$")
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![A-Z0-9])[A-Z]:[\\/]"
)
_WINDOWS_UNC_PATH = re.compile(
    r"(?i)(?:^|[\s\"'])\\\\[^\\/\s]+[\\/]"
)
_POSIX_HOST_PATH = re.compile(
    r"(?i)(?:^|[\s\"'=])/(?:home|users|root|var|tmp|opt|etc)/"
)
_ALLOWED_SOURCE_SCHEMES: Final = {
    "SEMANTIC_INPUT": frozenset({"semantic-input"}),
    "BLUEPRINT_EVIDENCE": frozenset({"capture"}),
    "NATIVE_EVIDENCE_SET": frozenset({"native-set"}),
}
_FORBIDDEN_SOURCE_SCHEMES: Final = frozenset(
    {"file", "http", "https", "ftp"}
)
_UNREAL_ENTITY_PREFIXES: Final = (
    "/Game/",
    "/Script/",
    "/Engine/",
    "/Plugin/",
    "/Plugins/",
    "/Mods/",
)


def _contains_host_path(value: object, *, allow_unreal: bool = False) -> bool:
    text = str(value or "")
    for candidate in {text, unquote(text)}:
        if (
            _WINDOWS_ABSOLUTE_PATH.search(candidate)
            or _WINDOWS_UNC_PATH.search(candidate)
            or _POSIX_HOST_PATH.search(candidate)
        ):
            return True
        if candidate.startswith("/") and not (
            allow_unreal
            and candidate.startswith(_UNREAL_ENTITY_PREFIXES)
        ):
            return True
        if "://" in candidate:
            parsed = urlsplit(candidate)
            if (
                parsed.scheme.casefold() == "file"
                or _WINDOWS_ABSOLUTE_PATH.search(parsed.path)
                or _POSIX_HOST_PATH.search(parsed.path)
            ):
                return True
    return False


def _validate_source_uri(source_kind: str, source_uri: str) -> None:
    if (
        not source_uri
        or "://" not in source_uri
        or any(character.isspace() for character in source_uri)
        or "\\" in source_uri
        or _contains_host_path(source_uri)
    ):
        raise ValueError("source manifest sourceUri is not path-free")
    parsed = urlsplit(source_uri)
    scheme = parsed.scheme.casefold()
    allowed = _ALLOWED_SOURCE_SCHEMES.get(source_kind)
    if (
        allowed is None
        or scheme in _FORBIDDEN_SOURCE_SCHEMES
        or scheme not in allowed
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or _POSIX_HOST_PATH.search(parsed.path)
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        raise ValueError("source manifest sourceUri scheme is invalid")


def _validate_generated_at(value: object) -> str:
    text = str(value or "").strip()
    parseable = (
        text[:-1] + "+00:00"
        if text.endswith("Z")
        else text
    )
    try:
        parsed = datetime.fromisoformat(parseable)
    except ValueError as exc:
        raise ValueError(
            "source manifest generatedAt is invalid"
        ) from exc
    if parsed.utcoffset() is None:
        raise ValueError("source manifest generatedAt requires a timezone")
    return text


@dataclass(frozen=True)
class SourceRevision:
    source_id: str
    source_kind: str
    source_uri: str
    fingerprint: str
    size_bytes: int = 0
    entity_uri: str = ""
    revision_label: str = ""

    def __post_init__(self) -> None:
        if (
            not _SOURCE_KIND.fullmatch(self.source_kind)
            or _contains_host_path(self.source_kind)
        ):
            raise ValueError("source manifest sourceKind is invalid")
        _validate_source_uri(self.source_kind, self.source_uri)
        if (
            not _HEX_SHA256.fullmatch(self.source_id)
            or self.source_id
            != source_id(self.source_kind, self.source_uri)
        ):
            raise ValueError("source manifest sourceId is invalid")
        if not _HEX_SHA256.fullmatch(self.fingerprint):
            raise ValueError("source manifest fingerprint is invalid")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("source manifest sizeBytes is invalid")
        if _contains_host_path(self.entity_uri, allow_unreal=True):
            raise ValueError("source manifest entityUri is not path-free")
        if _contains_host_path(self.revision_label):
            raise ValueError("source manifest revisionLabel is not path-free")

    def payload(self) -> dict[str, object]:
        return {
            "sourceId": self.source_id,
            "sourceKind": self.source_kind,
            "sourceUri": self.source_uri,
            "fingerprint": self.fingerprint,
            "sizeBytes": self.size_bytes,
            "entityUri": self.entity_uri,
            "revisionLabel": self.revision_label,
        }


@dataclass(frozen=True)
class SourceManifest:
    entries: tuple[SourceRevision, ...]
    generated_at: str
    schema: str = SOURCE_MANIFEST_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SOURCE_MANIFEST_SCHEMA:
            raise ValueError("source manifest schema is invalid")
        _validate_generated_at(self.generated_at)
        if len({item.source_id for item in self.entries}) != len(
            self.entries
        ):
            raise ValueError("source manifest has duplicate sourceId")

    def payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "generatedAt": self.generated_at,
            "entries": [
                value.payload()
                for value in sorted(
                    self.entries,
                    key=lambda item: item.source_id,
                )
            ],
        }

    @property
    def fingerprint(self) -> str:
        stable = self.payload()
        stable.pop("generatedAt")
        return hashlib.sha256(
            json.dumps(
                stable,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class SourceChange:
    change_kind: str
    source_id: str
    previous: SourceRevision | None
    current: SourceRevision | None

    def payload(self) -> dict[str, object]:
        return {
            "changeKind": self.change_kind,
            "sourceId": self.source_id,
            "previous": (
                self.previous.payload()
                if self.previous is not None
                else None
            ),
            "current": (
                self.current.payload()
                if self.current is not None
                else None
            ),
        }


@dataclass(frozen=True)
class SourceDiff:
    added: tuple[SourceChange, ...] = ()
    changed: tuple[SourceChange, ...] = ()
    deleted: tuple[SourceChange, ...] = ()
    schema: str = SOURCE_DIFF_SCHEMA

    @property
    def all_changes(self) -> tuple[SourceChange, ...]:
        return (*self.added, *self.changed, *self.deleted)

    @property
    def is_empty(self) -> bool:
        return not self.all_changes

    def payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "added": [value.payload() for value in self.added],
            "changed": [value.payload() for value in self.changed],
            "deleted": [value.payload() for value in self.deleted],
        }


def source_id(source_kind: str, source_uri: str) -> str:
    return hashlib.sha256(
        f"{source_kind}\0{source_uri}".encode("utf-8")
    ).hexdigest()


def _entry_from_payload(value: object) -> SourceRevision:
    if not isinstance(value, dict):
        raise ValueError("source manifest entry must be an object")
    expected = {
        "sourceId",
        "sourceKind",
        "sourceUri",
        "fingerprint",
        "sizeBytes",
        "entityUri",
        "revisionLabel",
    }
    if set(value) != expected:
        raise ValueError("source manifest entry fields are invalid")
    size = value["sizeBytes"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError("source manifest sizeBytes is invalid")
    entry = SourceRevision(
        source_id=str(value["sourceId"]),
        source_kind=str(value["sourceKind"]),
        source_uri=str(value["sourceUri"]),
        fingerprint=str(value["fingerprint"]),
        size_bytes=size,
        entity_uri=str(value["entityUri"]),
        revision_label=str(value["revisionLabel"]),
    )
    return entry


def source_manifest_from_payload(value: object) -> SourceManifest:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "generatedAt", "entries"}
        or value.get("schema") != SOURCE_MANIFEST_SCHEMA
        or not isinstance(value.get("entries"), list)
    ):
        raise ValueError("source manifest is invalid")
    entries = tuple(
        _entry_from_payload(item) for item in value["entries"]
    )
    if len({item.source_id for item in entries}) != len(entries):
        raise ValueError("source manifest has duplicate sourceId")
    return SourceManifest(
        entries=entries,
        generated_at=_validate_generated_at(value["generatedAt"]),
    )


def source_manifest_binding(
    manifest: SourceManifest,
) -> dict[str, object]:
    return {
        "schema": SOURCE_BINDING_SCHEMA,
        "sourceManifestFingerprint": manifest.fingerprint,
        "sourceManifest": manifest.payload(),
    }


def source_manifest_from_binding(value: object) -> SourceManifest:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "sourceManifestFingerprint",
            "sourceManifest",
        }
        or value.get("schema") != SOURCE_BINDING_SCHEMA
    ):
        raise ValueError("source manifest binding is invalid")
    manifest = source_manifest_from_payload(value["sourceManifest"])
    if value["sourceManifestFingerprint"] != manifest.fingerprint:
        raise ValueError("source manifest binding fingerprint is invalid")
    return manifest


def compare_source_manifests(
    previous: SourceManifest | None,
    current: SourceManifest,
) -> SourceDiff:
    old = {
        item.source_id: item
        for item in (() if previous is None else previous.entries)
    }
    new = {item.source_id: item for item in current.entries}
    return SourceDiff(
        added=tuple(
            SourceChange("ADDED", key, None, new[key])
            for key in sorted(new.keys() - old.keys())
        ),
        changed=tuple(
            SourceChange("CHANGED", key, old[key], new[key])
            for key in sorted(old.keys() & new.keys())
            if old[key] != new[key]
        ),
        deleted=tuple(
            SourceChange("DELETED", key, old[key], None)
            for key in sorted(old.keys() - new.keys())
        ),
    )


def _hash_file(path: Path, digest: object) -> None:
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            digest.update(chunk)


def runtime_observations_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.is_dir():
        digest.update(b"NOT_AVAILABLE")
        return digest.hexdigest()
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        _hash_file(path, digest)
        digest.update(b"\n")
    return digest.hexdigest()


def _capture_identity(database: Path) -> tuple[str, str]:
    try:
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro",
            uri=True,
        )
        try:
            rows = list(
                connection.execute(
                    """
                    SELECT DISTINCT object_path, revision_id
                    FROM asset_revisions
                    ORDER BY revision_id DESC
                    LIMIT 2
                    """
                )
            )
            if len(rows) != 1:
                return "", ""
            return str(rows[0][0] or ""), str(rows[0][1] or "")
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        return "", ""


def _blueprint_revision(
    database: Path,
    capture_root: Path,
) -> SourceRevision:
    entity_uri, revision_label = _capture_identity(database)
    asset_root = database.parent.parent
    asset_name = quote(
        asset_root.relative_to(capture_root).as_posix(),
        safe="._-",
    )
    source_kind = "BLUEPRINT_EVIDENCE"
    source_uri = f"capture://{asset_name}"
    digest = hashlib.sha256()
    for path in (database, database.parent / "manifest.json"):
        if not path.is_file():
            continue
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        _hash_file(path, digest)
        digest.update(b"\n")
    return SourceRevision(
        source_id=source_id(source_kind, source_uri),
        source_kind=source_kind,
        source_uri=source_uri,
        fingerprint=digest.hexdigest(),
        size_bytes=database.stat().st_size,
        entity_uri=entity_uri,
        revision_label=revision_label,
    )


def _native_revision(evidence_set: NativeEvidenceSet) -> SourceRevision:
    payload = asdict(evidence_set)
    fingerprint = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return SourceRevision(
        source_id=source_id(
            "NATIVE_EVIDENCE_SET",
            evidence_set.evidence_set_id,
        ),
        source_kind="NATIVE_EVIDENCE_SET",
        source_uri=evidence_set.evidence_set_id,
        fingerprint=fingerprint,
        revision_label=evidence_set.recipe_id,
    )


def scan_source_manifest(
    *,
    semantic_input_hashes: Mapping[str, str],
    capture_root: Path,
    native_root: Path,
    runtime_root: Path,
    generated_at: str,
) -> SourceManifest:
    """Build one stable manifest without recording host filesystem paths."""

    hashes = {
        str(key): str(value)
        for key, value in semantic_input_hashes.items()
    }
    if set(hashes) != SNAPSHOT_SEMANTIC_INPUT_KEYS:
        raise ValueError(
            "snapshot semantic input fingerprint set is incomplete"
        )
    entries = [
        SourceRevision(
            source_id=source_id(
                "SEMANTIC_INPUT",
                f"semantic-input://{key}",
            ),
            source_kind="SEMANTIC_INPUT",
            source_uri=f"semantic-input://{key}",
            fingerprint=hashes[key],
        )
        for key in sorted(SNAPSHOT_SEMANTIC_INPUT_KEYS)
    ]
    runtime_key = "runtimeObservations"
    entries.append(
        SourceRevision(
            source_id=source_id(
                "SEMANTIC_INPUT",
                f"semantic-input://{runtime_key}",
            ),
            source_kind="SEMANTIC_INPUT",
            source_uri=f"semantic-input://{runtime_key}",
            fingerprint=runtime_observations_sha256(runtime_root),
        )
    )
    capture_root = capture_root.resolve()
    if capture_root.is_dir():
        entries.extend(
            _blueprint_revision(database, capture_root)
            for database in sorted(
                capture_root.glob("*/evidence/evidence.sqlite"),
                key=lambda path: path.relative_to(capture_root).as_posix(),
            )
        )
    corpus = load_native_evidence_corpus(native_root)
    entries.extend(
        _native_revision(evidence_set)
        for evidence_set in corpus.evidence_sets
    )
    if len({entry.source_id for entry in entries}) != len(entries):
        raise ValueError("source manifest has duplicate source identities")
    return SourceManifest(
        entries=tuple(sorted(entries, key=lambda item: item.source_id)),
        generated_at=generated_at,
    )


__all__ = [
    "SNAPSHOT_SEMANTIC_INPUT_KEYS",
    "SOURCE_BINDING_SCHEMA",
    "SOURCE_DIFF_SCHEMA",
    "SOURCE_MANIFEST_SCHEMA",
    "SourceChange",
    "SourceDiff",
    "SourceManifest",
    "SourceRevision",
    "compare_source_manifests",
    "runtime_observations_sha256",
    "scan_source_manifest",
    "source_id",
    "source_manifest_binding",
    "source_manifest_from_binding",
    "source_manifest_from_payload",
]
